import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.models.vacancy import DEFAULT_SCORING_WEIGHTS, Vacancy
from app.schemas.vacancy import EducationLevel, VacancyCreate, VacancyRead, VacancyUpdate
from app.services.embeddings.text_builders import (
    build_vacancy_embedding_text,
    build_vacancy_responsibilities_text,
)
from app.services.embeddings.voyage_client import EmbeddingError, embed_text
from app.services.parsing.canonicalizer import classify_and_canonicalize_or_fallback
from app.services.parsing.jd_structurer import JDStructuringError, structure_vacancy

router = APIRouter(prefix="/vacancies", tags=["vacancies"])

# Fields that, when changed, invalidate the vacancy's embedding
EMBEDDING_RELEVANT_FIELDS = {
    "title",
    "description",
    "responsibilities",
    "required_skills",
    "preferred_skills",
    "min_experience_years",
    "education_requirement",
    "accepts_current_students",
    "graduation_year_min",
    "graduation_year_max",
}


def _compute_skill_canonicals(vacancy: Vacancy) -> None:
    """Derives English canonical forms, a hard/soft classification, and a
    language code (for language requirements) for required/preferred skills,
    so the (rule-based) skill matcher can bridge across languages, exclude
    soft skills from scoring, and match language requirements without
    depending on language_matcher's alias-spelling list - see
    canonicalizer.py. Best-effort: falls back to the original phrases (no
    cross-language benefit), "hard" (still scored), and no language code
    (falls back to the alias list) rather than blocking the request or
    silently dropping a requirement."""
    (
        vacancy.required_skills_canonical,
        vacancy.required_skills_type,
        vacancy.required_skills_language_code,
    ) = classify_and_canonicalize_or_fallback(vacancy.required_skills or [])
    (
        vacancy.preferred_skills_canonical,
        vacancy.preferred_skills_type,
        vacancy.preferred_skills_language_code,
    ) = classify_and_canonicalize_or_fallback(vacancy.preferred_skills or [])


def _compute_vacancy_embedding(vacancy: Vacancy) -> None:
    """Computes both vacancy embeddings: the whole-posting one (drives the
    overall semantic score) and the responsibilities-only one (drives per-job
    relevance weighting during scoring).

    Best-effort - a failed embedding call (e.g. missing VOYAGE_API_KEY) doesn't
    block vacancy creation/editing, just leaves it un-embedded until retried
    via POST /vacancies/{id}/recompute-embedding."""
    try:
        vacancy.embedding = embed_text(build_vacancy_embedding_text(vacancy))
        vacancy.responsibilities_embedding = embed_text(build_vacancy_responsibilities_text(vacancy))
        vacancy.embedding_error = None
    except EmbeddingError as e:
        vacancy.embedding = None
        vacancy.responsibilities_embedding = None
        vacancy.embedding_error = str(e)


def _safe_education_level(value: str | None) -> str:
    """Validates an AI-extracted education level against the known tiers,
    falling back to "none" rather than raising on an unexpected value."""
    if value is None:
        return EducationLevel.none.value
    try:
        return EducationLevel(value).value
    except ValueError:
        return EducationLevel.none.value


@router.post("", response_model=VacancyRead, status_code=status.HTTP_201_CREATED)
async def create_vacancy(
    payload: VacancyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Only `description` is required - paste the raw job posting and every
    other field (title, responsibilities, required/preferred skills,
    min_experience_years, education_requirement, enrollment-acceptance) is
    auto-extracted from it unless explicitly provided in the payload, in
    which case the provided value wins and extraction is skipped for that
    field specifically."""
    weights = payload.scoring_weights.model_dump() if payload.scoring_weights else dict(DEFAULT_SCORING_WEIGHTS)

    # Only fields the recruiter left unset need the AI to fill them in.
    fields_needing_extraction = [
        payload.title,
        payload.responsibilities,
        payload.required_skills,
        payload.preferred_skills,
        payload.min_experience_years,
        payload.education_requirement,
        payload.accepts_current_students,
    ]
    extracted: dict = {}
    extraction_error: str | None = None
    if any(v is None for v in fields_needing_extraction):
        try:
            extracted = structure_vacancy(payload.description)
        except JDStructuringError as e:
            extraction_error = str(e)

    vacancy = Vacancy(
        title=payload.title if payload.title is not None else (extracted.get("title") or "Untitled Vacancy"),
        description=payload.description,
        responsibilities=payload.responsibilities if payload.responsibilities is not None else extracted.get("responsibilities"),
        required_skills=payload.required_skills if payload.required_skills is not None else (extracted.get("required_skills") or []),
        preferred_skills=payload.preferred_skills if payload.preferred_skills is not None else (extracted.get("preferred_skills") or []),
        min_experience_years=payload.min_experience_years if payload.min_experience_years is not None else (extracted.get("min_experience_years") or 0.0),
        education_requirement=(
            payload.education_requirement.value
            if payload.education_requirement is not None
            else _safe_education_level(extracted.get("education_requirement"))
        ),
        accepts_current_students=payload.accepts_current_students if payload.accepts_current_students is not None else bool(extracted.get("accepts_current_students")),
        graduation_year_min=payload.graduation_year_min if payload.graduation_year_min is not None else extracted.get("graduation_year_min"),
        graduation_year_max=payload.graduation_year_max if payload.graduation_year_max is not None else extracted.get("graduation_year_max"),
        wage_min=payload.wage_min,
        wage_max=payload.wage_max,
        wage_currency=payload.wage_currency or "AZN",
        scoring_weights=weights,
        extraction_error=extraction_error,
        created_by=current_user.id,
    )
    db.add(vacancy)
    await db.flush()  # vacancy fields are set, but not yet committed

    _compute_skill_canonicals(vacancy)
    _compute_vacancy_embedding(vacancy)

    await db.commit()
    await db.refresh(vacancy)
    return vacancy


@router.get("", response_model=list[VacancyRead])
async def list_vacancies(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Team-visible: any recruiter can see all vacancies (shared candidate pool
    # per vacancy). Only the creator can edit/delete - see below.
    result = await db.execute(select(Vacancy).order_by(Vacancy.created_at.desc()))
    return result.scalars().all()


@router.get("/{vacancy_id}", response_model=VacancyRead)
async def get_vacancy(
    vacancy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    vacancy = await db.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vacancy not found")
    return vacancy


@router.patch("/{vacancy_id}", response_model=VacancyRead)
async def update_vacancy(
    vacancy_id: uuid.UUID,
    payload: VacancyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    vacancy = await db.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vacancy not found")
    if vacancy.created_by != current_user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the recruiter who created this vacancy can edit it")

    data = payload.model_dump(exclude_unset=True)

    if "education_requirement" in data and data["education_requirement"] is not None:
        data["education_requirement"] = payload.education_requirement.value
    if "scoring_weights" in data and data["scoring_weights"] is not None:
        data["scoring_weights"] = payload.scoring_weights.model_dump()

    for field, value in data.items():
        setattr(vacancy, field, value)

    if {"required_skills", "preferred_skills"} & data.keys():
        _compute_skill_canonicals(vacancy)

    if EMBEDDING_RELEVANT_FIELDS & data.keys():
        _compute_vacancy_embedding(vacancy)

    await db.commit()
    await db.refresh(vacancy)
    return vacancy


@router.post("/{vacancy_id}/recompute-embedding", response_model=VacancyRead)
async def recompute_vacancy_embedding(
    vacancy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retries embedding generation - useful if it failed earlier (e.g.
    VOYAGE_API_KEY wasn't configured yet at creation time)."""
    vacancy = await db.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vacancy not found")

    _compute_vacancy_embedding(vacancy)

    await db.commit()
    await db.refresh(vacancy)
    return vacancy


@router.delete("/{vacancy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vacancy(
    vacancy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    vacancy = await db.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vacancy not found")
    if vacancy.created_by != current_user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the recruiter who created this vacancy can delete it")

    await db.delete(vacancy)
    await db.commit()
