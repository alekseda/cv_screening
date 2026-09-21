import asyncio
import uuid

import anyio
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.candidate import Candidate
from app.models.candidate_job_relevance import CandidateJobRelevance
from app.models.candidate_skill_relevance import CandidateSkillRelevance, hash_requirement
from app.models.match import Match
from app.models.user import User
from app.models.vacancy import Vacancy
from app.schemas.candidate import CandidateSummary
from app.schemas.match import MatchRead, RankedCandidate, ScoringRunResult
from app.services.scoring.engine import score_candidate
from app.services.scoring.relevance_judge import RelevanceJudgementError, judge_job_relevance
from app.services.scoring.skill_matcher import corpus_text, match_skill_list
from app.services.scoring.skill_relevance_judge import SkillRelevanceError, judge_skill_relevance

router = APIRouter(tags=["matching"])


# --- Job relevance: cache state / AI call / cache write, kept as three small
# pieces (rather than one function like the original version) so bulk scoring
# can run the (slow, blocking) AI call concurrently across candidates while
# keeping every DB read/write sequential - AsyncSession isn't safe to use from
# multiple concurrent tasks. See score_vacancy_candidates for the concurrent
# orchestration, and _job_relevances_for below for the simple sequential
# version single-candidate scoring still uses.

async def _job_relevance_state(
    db: AsyncSession, candidate, vacancy, force_refresh: bool = False
) -> tuple[dict[int, dict], list[dict] | None]:
    """(cached_relevances, jobs_needing_judgement). The second element is
    None when nothing needs judging - cached_relevances is already complete."""
    jobs = (candidate.structured_data or {}).get("work_history") or []
    if not jobs:
        return {}, None

    if force_refresh:
        await db.execute(
            delete(CandidateJobRelevance).where(
                CandidateJobRelevance.candidate_id == candidate.id,
                CandidateJobRelevance.vacancy_id == vacancy.id,
            )
        )
        return {}, jobs

    cached = (
        await db.execute(
            select(CandidateJobRelevance).where(
                CandidateJobRelevance.candidate_id == candidate.id,
                CandidateJobRelevance.vacancy_id == vacancy.id,
            )
        )
    ).scalars().all()
    if len(cached) == len(jobs):
        return {row.job_index: {"relevance": row.relevance, "reason": row.reason or ""} for row in cached}, None
    # Partial/stale cache (e.g. the CV was re-parsed and now has a different
    # number of jobs) - drop it and re-judge rather than mixing generations.
    if cached:
        await db.execute(
            delete(CandidateJobRelevance).where(
                CandidateJobRelevance.candidate_id == candidate.id,
                CandidateJobRelevance.vacancy_id == vacancy.id,
            )
        )
    return {}, jobs


def _run_job_judgement(jobs: list[dict], vacancy_title: str, vacancy_responsibilities: str) -> dict[int, dict]:
    """Pure wrapper around judge_job_relevance - no DB access, safe to run
    concurrently in a worker thread. Empty dict on failure, same fallback
    semantics as before: the engine then counts every job at full relevance
    rather than silently erasing the candidate's experience."""
    try:
        return judge_job_relevance(
            jobs, vacancy_title=vacancy_title, vacancy_responsibilities=vacancy_responsibilities
        )
    except RelevanceJudgementError:
        return {}


async def _store_job_relevance(db: AsyncSession, candidate, vacancy, judgements: dict[int, dict]) -> None:
    for job_index, judgement in judgements.items():
        db.add(
            CandidateJobRelevance(
                candidate_id=candidate.id,
                vacancy_id=vacancy.id,
                job_index=job_index,
                relevance=judgement["relevance"],
                reason=judgement.get("reason"),
            )
        )
    await db.flush()


async def _job_relevances_for(
    db: AsyncSession, candidate, vacancy, force_refresh: bool = False
) -> dict[int, dict]:
    """Per-job relevance judgements for this candidate against this vacancy -
    the simple sequential path used by single-candidate scoring (bulk scoring
    uses _job_relevance_state/_run_job_judgement/_store_job_relevance directly
    so it can batch the AI calls concurrently across candidates).

    Cached in candidate_job_relevance: the (paid) judgement call runs once per
    candidate per vacancy, so re-scoring after a weight change is free. Pass
    force_refresh=True to re-judge, e.g. after editing the vacancy's
    responsibilities. The AI call itself runs in a worker thread
    (anyio.to_thread) so it doesn't block the event loop for other requests
    while it's in flight.
    """
    cached, jobs = await _job_relevance_state(db, candidate, vacancy, force_refresh)
    if jobs is None:
        return cached

    judgements = await anyio.to_thread.run_sync(
        _run_job_judgement, jobs, vacancy.title, vacancy.responsibilities or vacancy.description
    )
    await _store_job_relevance(db, candidate, vacancy, judgements)
    return judgements


def _failed_hard_requirements(vacancy, structured: dict) -> list[str]:
    """Runs the literal token pass standalone - same inputs score_candidate
    itself uses - purely to find which requirements need the semantic
    fallback judgement. Cheap, pure-Python, no I/O, so doing it twice (once
    here, once inside score_candidate) costs nothing but a little CPU."""
    failed: list[str] = []
    for requirements, canonicals, types, codes in (
        (
            vacancy.required_skills or [],
            vacancy.required_skills_canonical,
            vacancy.required_skills_type,
            vacancy.required_skills_language_code,
        ),
        (
            vacancy.preferred_skills or [],
            vacancy.preferred_skills_canonical,
            vacancy.preferred_skills_type,
            vacancy.preferred_skills_language_code,
        ),
    ):
        _, details = match_skill_list(requirements, canonicals, structured, types, codes)
        failed.extend(
            d["requirement"] for d in details if d.get("matched") is False and d.get("check") != "language"
        )
    return failed


# Same split as job relevance above, for the same reason.

async def _skill_relevance_state(
    db: AsyncSession, candidate, vacancy, force_refresh: bool = False
) -> tuple[dict[str, dict], list[str] | None]:
    """(cached_judgements, requirements_needing_judgement). The second
    element is None when nothing needs judging."""
    structured = candidate.structured_data or {}
    failed = _failed_hard_requirements(vacancy, structured)
    if not failed:
        return {}, None

    hashes = {hash_requirement(r): r for r in failed}

    if force_refresh:
        await db.execute(
            delete(CandidateSkillRelevance).where(
                CandidateSkillRelevance.candidate_id == candidate.id,
                CandidateSkillRelevance.vacancy_id == vacancy.id,
                CandidateSkillRelevance.requirement_hash.in_(hashes.keys()),
            )
        )
        return {}, failed

    cached = (
        await db.execute(
            select(CandidateSkillRelevance).where(
                CandidateSkillRelevance.candidate_id == candidate.id,
                CandidateSkillRelevance.vacancy_id == vacancy.id,
                CandidateSkillRelevance.requirement_hash.in_(hashes.keys()),
            )
        )
    ).scalars().all()
    result = {row.requirement: {"matched": row.matched, "reason": row.reason or ""} for row in cached}
    cached_hashes = {hash_requirement(r) for r in result}
    to_judge = [hashes[h] for h in hashes if h not in cached_hashes]
    return result, (to_judge or None)


def _run_skill_judgement(requirements: list[str], resume_text: str) -> dict[str, dict]:
    """Pure wrapper around judge_skill_relevance - no DB access, safe to run
    concurrently in a worker thread."""
    try:
        return judge_skill_relevance(requirements, resume_text)
    except SkillRelevanceError:
        return {}


async def _store_skill_relevance(db: AsyncSession, candidate, vacancy, judgements: dict[str, dict]) -> None:
    for requirement, judgement in judgements.items():
        db.add(
            CandidateSkillRelevance(
                candidate_id=candidate.id,
                vacancy_id=vacancy.id,
                requirement_hash=hash_requirement(requirement),
                requirement=requirement,
                matched=judgement["matched"],
                reason=judgement.get("reason"),
            )
        )
    await db.flush()


async def _skill_relevances_for(
    db: AsyncSession, candidate, vacancy, force_refresh: bool = False
) -> dict[str, dict]:
    """Semantic-fallback judgements for this candidate's requirements that
    failed literal token matching - the simple sequential path used by
    single-candidate scoring (bulk scoring uses _skill_relevance_state/
    _run_skill_judgement/_store_skill_relevance directly so it can batch the
    AI calls concurrently across candidates). See skill_matcher.
    apply_semantic_fallback and skill_relevance_judge.py.

    Cached in candidate_skill_relevance, keyed by a hash of the requirement
    text: the (paid) judgement call runs once per candidate per vacancy per
    requirement, so re-scoring after a weight change is free. Requirements
    that already match literally never reach here at all. On failure (or for
    any requirement the judge call didn't rate), that requirement is simply
    left out of the returned dict - the engine leaves it exactly as the
    literal matcher scored it (still "missing"), same as if this fallback
    didn't exist. The AI call runs in a worker thread (anyio.to_thread) so it
    doesn't block the event loop for other requests while in flight.
    """
    cached, to_judge = await _skill_relevance_state(db, candidate, vacancy, force_refresh)
    if to_judge is None:
        return cached

    judgements = await anyio.to_thread.run_sync(
        _run_skill_judgement, to_judge, corpus_text(candidate.structured_data or {})
    )
    await _store_skill_relevance(db, candidate, vacancy, judgements)
    cached.update(judgements)
    return cached


@router.post("/vacancies/{vacancy_id}/score", response_model=ScoringRunResult)
async def score_vacancy_candidates(
    vacancy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """(Re)scores every candidate against this vacancy, replacing any previous
    results for it.

    Scoring is a re-runnable action rather than a one-shot at upload time
    precisely because the weights are meant to be tuned: after adjusting a
    vacancy's scoring_weights (or editing its requirements), call this again
    to see the new ranking. Nothing is re-parsed or re-embedded - it reuses
    the stored structured data and vectors, so it's cheap to re-run.

    CONCURRENCY: the two AI judgement calls (job relevance, skill semantic
    fallback) are the slow part - one blocking network call each, per
    candidate, when not already cached. These now run concurrently across
    candidates, bounded by MAX_CONCURRENT_SCORING_CALLS worker threads,
    instead of one candidate at a time - the same "gather the slow work
    concurrently, then write to the DB in a fast sequential pass" pattern
    parse_and_store_many uses for uploads, and for the same reason: a single
    AsyncSession isn't safe to use from multiple concurrent tasks, so only
    the AI calls (no DB access) run inside the bounded/concurrent part.
    """
    vacancy = await db.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vacancy not found")

    candidates = (
        await db.execute(select(Candidate).where(Candidate.vacancy_id == vacancy_id))
    ).scalars().all()

    # Replace rather than accumulate: a vacancy/candidate pair has exactly one
    # current score, and stale rows from an earlier weighting would be misleading.
    await db.execute(delete(Match).where(Match.vacancy_id == vacancy_id))

    # Phase 1: figure out what's already cached vs. what needs judging - fast,
    # DB-only, sequential (a handful of indexed SELECTs per candidate).
    job_states = [await _job_relevance_state(db, candidate, vacancy) for candidate in candidates]
    skill_states = [await _skill_relevance_state(db, candidate, vacancy) for candidate in candidates]

    # Phase 2: run the actual (slow, blocking) AI calls concurrently, bounded.
    # No DB access happens in here - see the docstring above.
    semaphore = anyio.Semaphore(settings.MAX_CONCURRENT_SCORING_CALLS)

    async def _resolve_job(cached: dict, jobs: list[dict] | None) -> dict:
        if jobs is None:
            return cached
        async with semaphore:
            return await anyio.to_thread.run_sync(
                _run_job_judgement, jobs, vacancy.title, vacancy.responsibilities or vacancy.description
            )

    async def _resolve_skill(candidate, cached: dict, to_judge: list[str] | None) -> dict:
        if to_judge is None:
            return cached
        async with semaphore:
            judgements = await anyio.to_thread.run_sync(
                _run_skill_judgement, to_judge, corpus_text(candidate.structured_data or {})
            )
        return {**cached, **judgements}

    job_relevances_list, skill_relevances_list = await asyncio.gather(
        asyncio.gather(*(_resolve_job(cached, jobs) for cached, jobs in job_states)),
        asyncio.gather(
            *(
                _resolve_skill(candidate, cached, to_judge)
                for candidate, (cached, to_judge) in zip(candidates, skill_states)
            )
        ),
    )

    # Phase 3: write freshly-judged results to the cache tables and compute
    # scores - fast, sequential, the only part touching the DB again.
    scored = 0
    skipped_reasons: list[str] = []

    for candidate, (_, jobs), job_relevances, (_, to_judge), skill_relevances in zip(
        candidates, job_states, job_relevances_list, skill_states, skill_relevances_list
    ):
        if jobs is not None:
            await _store_job_relevance(db, candidate, vacancy, job_relevances)
        if to_judge is not None:
            await _store_skill_relevance(
                db, candidate, vacancy, {k: v for k, v in skill_relevances.items() if k in to_judge}
            )

        result = score_candidate(
            candidate, vacancy, job_relevances=job_relevances, skill_relevances=skill_relevances
        )

        # Unscorable candidates (unparsed file, non-CV upload) are still
        # recorded, at 0 with the reason attached - a recruiter needs to see
        # that a file was rejected, not have it silently vanish from the list.
        if not result["breakdown"].get("scorable", False):
            skipped_reasons.append(f"{candidate.original_filename}: {result['breakdown'].get('reason', '')}")

        db.add(
            Match(
                candidate_id=candidate.id,
                vacancy_id=vacancy_id,
                total_score=result["total_score"],
                hard_filter_score=result["hard_filter_score"],
                semantic_score=result["semantic_score"],
                score_breakdown=result["breakdown"],
            )
        )
        scored += 1

    await db.commit()

    return ScoringRunResult(
        vacancy_id=vacancy_id,
        scored=scored,
        skipped=len(skipped_reasons),
        skipped_reasons=skipped_reasons,
    )


@router.get("/vacancies/{vacancy_id}/ranking", response_model=list[RankedCandidate])
async def get_ranking(
    vacancy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Candidates ranked by total score, each with the full breakdown that
    produced it. Run POST /vacancies/{id}/score first (or after changing
    weights) to populate or refresh these."""
    vacancy = await db.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vacancy not found")

    rows = (
        await db.execute(
            select(Match, Candidate)
            .join(Candidate, Match.candidate_id == Candidate.id)
            .where(Match.vacancy_id == vacancy_id)
            .order_by(Match.total_score.desc())
        )
    ).all()

    if not rows:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No scores yet for this vacancy - run POST /vacancies/{vacancy_id}/score first",
        )

    return [
        RankedCandidate(
            candidate=CandidateSummary.model_validate(candidate),
            total_score=match.total_score,
            hard_filter_score=match.hard_filter_score,
            semantic_score=match.semantic_score,
            score_breakdown=match.score_breakdown,
            scored_at=match.created_at,
        )
        for match, candidate in rows
    ]


@router.get("/vacancies/{vacancy_id}/candidates/{candidate_id}/match", response_model=MatchRead)
async def get_single_match(
    vacancy_id: uuid.UUID,
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The stored score and full breakdown for one candidate/vacancy pair."""
    match = (
        await db.execute(
            select(Match).where(Match.vacancy_id == vacancy_id, Match.candidate_id == candidate_id)
        )
    ).scalar_one_or_none()

    if match is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No score for this candidate/vacancy pair - run POST /vacancies/{vacancy_id}/score first",
        )
    return match


@router.post("/vacancies/{vacancy_id}/candidates/{candidate_id}/score", response_model=MatchRead)
async def score_single_candidate(
    vacancy_id: uuid.UUID,
    candidate_id: uuid.UUID,
    refresh_relevance: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Scores (or re-scores) a single candidate - useful after entering their
    wage expectation or re-parsing their resume, without re-running the whole
    vacancy.

    EFFICIENCY FIX: this used to unconditionally force a fresh (paid)
    relevance judgement call on every single re-score, even for changes -
    like a wage update - that have nothing to do with job relevance.
    `_job_relevances_for`'s own cache is already correctly invalidated when a
    reparse changes the job count, so cache-first is the default now, exactly
    as safe as the vacancy-wide scoring endpoint above, and it skips a wasted
    AI call on the common case (wage edits, weight tuning) of re-scoring one
    candidate.

    Pass ?refresh_relevance=true to force a fresh judgement anyway - e.g.
    after editing this vacancy's responsibilities, since a relevance cache
    keyed only by (candidate, vacancy) doesn't automatically know the
    responsibilities text underneath it changed.
    """
    vacancy = await db.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vacancy not found")

    candidate = await db.get(Candidate, candidate_id)
    if candidate is None or candidate.vacancy_id != vacancy_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found for this vacancy")

    job_relevances = await _job_relevances_for(db, candidate, vacancy, force_refresh=refresh_relevance)
    skill_relevances = await _skill_relevances_for(db, candidate, vacancy, force_refresh=refresh_relevance)
    result = score_candidate(
        candidate, vacancy, job_relevances=job_relevances, skill_relevances=skill_relevances
    )

    await db.execute(
        delete(Match).where(Match.vacancy_id == vacancy_id, Match.candidate_id == candidate_id)
    )
    match = Match(
        candidate_id=candidate_id,
        vacancy_id=vacancy_id,
        total_score=result["total_score"],
        hard_filter_score=result["hard_filter_score"],
        semantic_score=result["semantic_score"],
        score_breakdown=result["breakdown"],
    )
    db.add(match)
    await db.commit()
    await db.refresh(match)
    return match
