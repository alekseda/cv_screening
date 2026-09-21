import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.db.base import Base

# Default scoring weights. Stored per-vacancy so recruiters can tune them
# without a code change - these are just the starting values for new vacancies.
# Scoring is a FLAT weighted sum of six components, each scored 0-1 and
# multiplied by its weight (all six must sum to 1.0). This replaced an earlier
# two-tier "hard filter vs semantic" split: once responsibilities-match became
# its own weighted component, the two-tier structure would have counted it
# twice - once as a component and again inside an overall semantic score.
#
# Every weight is per-vacancy configurable; these are just the defaults.
DEFAULT_SCORING_WEIGHTS = {
    "responsibilities_weight": 0.45,   # how well the candidate's actual work matches what the role involves
    "wage_weight": 0.15,               # salary expectation vs budget
    "experience_weight": 0.15,         # relevance-weighted years vs the minimum
    "required_skills_weight": 0.10,
    "education_weight": 0.10,
    "preferred_skills_weight": 0.05,   # deliberately smallest: a bonus, not a gate
}


class Vacancy(Base):
    """A job vacancy. Most fields can either be entered manually or
    auto-extracted from a pasted job description via jd_structurer.py -
    whichever the recruiter didn't supply gets filled in from that extraction.
    Everything remains editable afterward via PATCH regardless of its origin."""

    __tablename__ = "vacancies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)  # raw JD text, used for embedding + extraction
    responsibilities: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Explicit structured requirements (recruiter-entered or AI-extracted, drives hard filters)
    required_skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferred_skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    # English canonical form of each entry above, same order/count, auto-derived
    # regardless of whether required_skills/preferred_skills came from AI
    # extraction or manual entry - see canonicalizer.py. Exists purely to let
    # the (deliberately non-semantic) skill matcher bridge across languages;
    # never user-editable directly.
    required_skills_canonical: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferred_skills_canonical: Mapped[list[str]] = mapped_column(JSON, default=list)
    # "hard" or "soft" per entry, same order/count as required_skills/preferred_skills
    # above, auto-derived alongside the canonical forms - see canonicalizer.py.
    # Soft skills (communication, teamwork, etc.) are excluded from
    # required_skills_weight/preferred_skills_weight scoring - see skill_matcher.py.
    # Never user-editable directly.
    required_skills_type: Mapped[list[str]] = mapped_column(JSON, default=list)
    preferred_skills_type: Mapped[list[str]] = mapped_column(JSON, default=list)
    # ISO 639-1 code per entry (or null) if that requirement is a language
    # requirement, same order/count as required_skills/preferred_skills above,
    # auto-derived alongside canonical/type - see canonicalizer.py. Lets
    # language_matcher.py skip its own spelling-alias guesswork when this is
    # set; falls back to it when null (e.g. AI classification failed). Never
    # user-editable directly.
    required_skills_language_code: Mapped[list[str | None]] = mapped_column(JSON, default=list)
    preferred_skills_language_code: Mapped[list[str | None]] = mapped_column(JSON, default=list)
    min_experience_years: Mapped[float] = mapped_column(Float, default=0.0)
    education_requirement: Mapped[str] = mapped_column(
        String(50), default="none"
    )  # "none" | "high_school" | "bachelor" | "master" | "phd"

    # Lets a posting also accept candidates who haven't completed
    # education_requirement's degree yet (e.g. "final-year student or
    # 2024/2025 graduate") - see Candidate.currently_enrolled/current_degree_level.
    accepts_current_students: Mapped[bool] = mapped_column(default=False, nullable=False)
    graduation_year_min: Mapped[int | None] = mapped_column(nullable=True)
    graduation_year_max: Mapped[int | None] = mapped_column(nullable=True)

    # Budget range for the role. Both null = no wage requirement configured,
    # which the scoring engine treats as neutral (full credit) rather than
    # penalising candidates - same principle as min_experience_years=0.
    wage_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    wage_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    wage_currency: Mapped[str] = mapped_column(String(10), default="AZN", nullable=False)

    # Configurable scoring weights (see DEFAULT_SCORING_WEIGHTS above)
    scoring_weights: Mapped[dict] = mapped_column(JSON, default=lambda: dict(DEFAULT_SCORING_WEIGHTS))

    # Embedding of `description` (+ requirements), populated in Phase 3.
    # Drives the overall semantic score (candidate profile <-> whole vacancy).
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.EMBEDDING_DIM), nullable=True)
    # Separate embedding of `responsibilities` alone (Phase 4). Used ONLY for
    # per-job relevance weighting: each of a candidate's jobs is compared against
    # what the role actually involves day-to-day, rather than against the whole
    # posting (which includes requirements/boilerplate that would muddy the signal).
    responsibilities_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.EMBEDDING_DIM), nullable=True
    )
    embedding_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set if the JD auto-extraction call failed (e.g. missing ANTHROPIC_API_KEY) -
    # any fields the recruiter didn't manually provide will have fallen back to
    # defaults and should be filled in by hand via PATCH.
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Links this vacancy to a job posting in Glorri.az (its `job.id` from the
    # export - see services/integrations/glorri_client.py), so
    # POST /vacancies/{id}/sync-glorri knows which job's applicants to pull.
    # Set manually via PATCH; null means syncing isn't configured for this
    # vacancy yet.
    glorri_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
