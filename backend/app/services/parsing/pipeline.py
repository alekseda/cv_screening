"""
Ties together: save uploaded file -> extract raw text (with OCR fallback) ->
structure via Claude -> persist both raw_text and structured_data on the
Candidate row.

EFFICIENCY PASS NOTES (cost + time reduction, no accuracy change):
  1. `_sync_job_embeddings` no longer makes per-job Voyage calls - that data
     was superseded by AI-judged relevance and nothing reads it (see the
     function's own docstring below).
  2. The blocking extract -> structure -> embed work (`_run_extract_and_structure`)
     is now a pure function with no DB access, so `parse_and_store_many` can run
     it concurrently across many files in worker threads (anyio.to_thread),
     bounded by a semaphore. This is what turns N sequential AI round-trips
     into ceil(N / MAX_CONCURRENT_PARSES) rounds of wall-clock time for a bulk
     upload, without adding a new job queue / infra dependency.
  3. `apply_cached_parse` lets an identical file (by content hash) uploaded to
     a second vacancy skip extraction/structuring/embedding entirely and reuse
     the first parse's results - see routes/candidates.py for where this is
     checked before the pipeline runs at all.

Kept as plain functions (not a class) since each step is independently
useful - e.g. /candidates/{id}/reparse only needs the structuring step,
re-run against the raw_text already stored, without touching the file at all.
"""
import asyncio
import os
import uuid
from dataclasses import dataclass

import anyio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.candidate import Candidate
from app.models.candidate_job_embedding import CandidateJobEmbedding
from app.services.embeddings.text_builders import build_candidate_embedding_text
from app.services.embeddings.voyage_client import EmbeddingError, embed_text
from app.services.parsing.ai_structurer import ResumeStructuringError, structure_resume
from app.services.parsing.canonicalizer import canonicalize_or_fallback
from app.services.parsing.experience_calculator import compute_total_experience
from app.services.parsing.text_extraction import TextExtractionError, extract_text

# Bounded concurrency for bulk uploads: enough to overlap network-bound AI/OCR
# calls meaningfully, low enough not to hammer the Anthropic/Voyage rate
# limits or spike memory from many PDFs/images in flight at once. Tunable via
# env if a given deployment's rate limits allow more headroom.
MAX_CONCURRENT_PARSES = settings.MAX_CONCURRENT_PARSES


def save_upload_file(vacancy_id: uuid.UUID, candidate_id: uuid.UUID, filename: str, content: bytes) -> str:
    """Saves the raw upload to disk under RESUME_STORAGE_DIR/{vacancy_id}/{candidate_id}.{ext}
    and returns the path. Original filename is kept separately in the DB for display."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    dir_path = os.path.join(settings.RESUME_STORAGE_DIR, str(vacancy_id))
    os.makedirs(dir_path, exist_ok=True)
    dest_path = os.path.join(dir_path, f"{candidate_id}.{ext}")
    with open(dest_path, "wb") as f:
        f.write(content)
    return dest_path


@dataclass
class PipelineResult:
    """Outcome of the blocking extract -> structure -> embed pipeline for one
    file. Pure data, no DB access - safe to produce inside a worker thread."""
    ok: bool
    raw_text: str | None = None
    structured: dict | None = None
    embedding: list[float] | None = None
    embedding_error: str | None = None
    error: str | None = None


def _structure_and_embed(raw_text: str) -> PipelineResult:
    """AI-structure already-extracted text, compute derived fields, and embed
    the resulting profile. No file/OCR I/O here - used both by the full
    pipeline and by reparse (which starts from stored raw_text)."""
    try:
        structured = structure_resume(raw_text)
    except ResumeStructuringError as e:
        return PipelineResult(ok=False, raw_text=raw_text, error=str(e))
    except Exception as e:  # noqa: BLE001 - a bad resume must not crash a batch upload
        return PipelineResult(ok=False, raw_text=raw_text, error=f"Unexpected error: {e}")

    # Total experience (work + volunteer) is computed here in Python from the
    # AI-extracted date ranges, not trusted from the AI's own arithmetic - see
    # experience_calculator.py for why. Written back into `structured` so
    # structured_data stays a complete, self-consistent record.
    work_years, work_is_estimate = compute_total_experience(structured.get("work_history") or [])
    volunteer_years, volunteer_is_estimate = compute_total_experience(structured.get("volunteer_experience") or [])
    structured["estimated_total_work_experience_years"] = work_years
    structured["total_work_experience_is_estimate"] = work_is_estimate
    structured["estimated_total_volunteer_experience_years"] = volunteer_years
    structured["total_volunteer_experience_is_estimate"] = volunteer_is_estimate

    # English canonical form of each skill, same order/count as skills - lets
    # the (rule-based) skill matcher bridge across languages. Best-effort:
    # falls back to the original skills list if canonicalization fails.
    structured["skills_canonical"] = canonicalize_or_fallback(structured.get("skills") or [])

    embedding: list[float] | None = None
    embedding_error: str | None = None
    try:
        embedding = embed_text(build_candidate_embedding_text(structured))
    except EmbeddingError as e:
        # Failure here doesn't invalidate the parse; the structured data is
        # still fully usable, just not semantically searchable until retried.
        embedding_error = str(e)

    return PipelineResult(
        ok=True, raw_text=raw_text, structured=structured,
        embedding=embedding, embedding_error=embedding_error,
    )


def _run_extract_and_structure(file_path: str, original_filename: str) -> PipelineResult:
    """Full blocking pipeline for one file: extract -> structure -> embed.
    No DB access - this is the unit of work parallelized across threads for
    bulk uploads (see parse_and_store_many)."""
    try:
        raw_text = extract_text(file_path, original_filename)
    except TextExtractionError as e:
        return PipelineResult(ok=False, error=str(e))
    return _structure_and_embed(raw_text)


def _apply_pipeline_result(candidate: Candidate, result: PipelineResult) -> None:
    """Copies a PipelineResult onto a Candidate row. Pure attribute
    assignment, no I/O - safe and fast to run sequentially after concurrent
    pipeline work has already happened off-thread."""
    if result.raw_text is not None:
        candidate.raw_text = result.raw_text

    if not result.ok:
        candidate.parsing_status = "failed"
        candidate.parsing_error = result.error
        return

    structured = result.structured
    candidate.structured_data = structured
    candidate.document_type = structured.get("document_type")
    candidate.detected_language = structured.get("detected_language")
    candidate.total_work_experience_years = structured.get("estimated_total_work_experience_years")
    candidate.total_work_experience_is_estimate = structured.get("total_work_experience_is_estimate", False)
    candidate.total_volunteer_experience_years = structured.get("estimated_total_volunteer_experience_years")
    candidate.total_volunteer_experience_is_estimate = structured.get("total_volunteer_experience_is_estimate", False)
    candidate.age = structured.get("age")
    candidate.date_of_birth = structured.get("date_of_birth")
    candidate.height_cm = structured.get("height_cm")
    candidate.education_level = structured.get("education_level")
    candidate.highest_completed_degree_graduation_year = structured.get("highest_completed_degree_graduation_year")
    candidate.military_service = structured.get("military_service")
    candidate.currently_enrolled = structured.get("currently_enrolled")
    candidate.current_degree_level = structured.get("current_degree_level")
    candidate.current_year_of_study = structured.get("current_year_of_study")
    candidate.expected_graduation_year = structured.get("expected_graduation_year")
    candidate.embedding = result.embedding
    candidate.embedding_error = result.embedding_error
    candidate.parsing_status = "success"
    candidate.parsing_error = None


def apply_cached_parse(candidate: Candidate, source: Candidate) -> None:
    """Copies a previously-parsed Candidate's results onto a new row for a
    different vacancy upload of the IDENTICAL file (same content hash) -
    skips extraction, AI structuring, and embedding entirely.

    This is the single largest cost/time lever in the pipeline: the same
    resume submitted to several vacancy postings (a very common real-world
    pattern - candidates apply broadly) now costs one parse total instead of
    one parse per vacancy it happens to land in. See routes/candidates.py for
    where the content-hash lookup happens before the pipeline is ever invoked.
    """
    candidate.raw_text = source.raw_text
    candidate.structured_data = source.structured_data
    candidate.document_type = source.document_type
    candidate.detected_language = source.detected_language
    candidate.total_work_experience_years = source.total_work_experience_years
    candidate.total_work_experience_is_estimate = source.total_work_experience_is_estimate
    candidate.total_volunteer_experience_years = source.total_volunteer_experience_years
    candidate.total_volunteer_experience_is_estimate = source.total_volunteer_experience_is_estimate
    candidate.age = source.age
    candidate.date_of_birth = source.date_of_birth
    candidate.height_cm = source.height_cm
    candidate.education_level = source.education_level
    candidate.highest_completed_degree_graduation_year = source.highest_completed_degree_graduation_year
    candidate.military_service = source.military_service
    candidate.currently_enrolled = source.currently_enrolled
    candidate.current_degree_level = source.current_degree_level
    candidate.current_year_of_study = source.current_year_of_study
    candidate.expected_graduation_year = source.expected_graduation_year
    candidate.embedding = source.embedding
    candidate.embedding_error = source.embedding_error
    candidate.parsing_status = "success"
    candidate.parsing_error = None


async def _sync_job_embeddings(db: AsyncSession, candidate: Candidate) -> None:
    """EFFICIENCY NOTE (cost reduction pass): this used to embed every job in a
    candidate's work_history individually via Voyage (one API call per job).
    That approach was superseded by AI-judged relevance (see
    services/scoring/relevance_judge.py's docstring: "Why this replaced
    embedding cosine similarity") - the resulting `candidate_job_embeddings`
    rows have not been read by any query in the codebase since. Per-job
    embedding calls were still being made on every parse, silently paying for
    and storing data nothing consumes.

    This function is now a deliberate no-op: it clears any legacy rows so old
    data doesn't linger, and returns without making any embedding calls.
    Removing it entirely (rather than just skipping the call) is left for a
    follow-up migration that drops the candidate_job_embeddings table - kept
    as-is here to avoid touching the schema in an efficiency pass.

    Net effect: N-1 fewer embedding calls per candidate with N jobs, zero
    change to any scored output (this data was never an input to scoring).
    """
    await db.execute(delete(CandidateJobEmbedding).where(CandidateJobEmbedding.candidate_id == candidate.id))


async def parse_and_store(db: AsyncSession, candidate: Candidate) -> Candidate:
    """Runs the full pipeline for ONE candidate and commits the result -
    success or failure - onto the row. Never raises; failures are recorded on
    the candidate so a bad file doesn't break a batch upload.

    The blocking work runs in a worker thread (anyio.to_thread) so it doesn't
    block the event loop for other concurrent requests, even for a single
    upload. For many files at once, prefer parse_and_store_many.
    """
    result = await anyio.to_thread.run_sync(
        _run_extract_and_structure, candidate.file_path, candidate.original_filename
    )
    _apply_pipeline_result(candidate, result)
    await _sync_job_embeddings(db, candidate)  # no-op; clears legacy rows only
    await db.commit()
    await db.refresh(candidate)
    return candidate


async def parse_and_store_many(db: AsyncSession, candidates: list[Candidate]) -> list[Candidate]:
    """Runs the blocking extract -> structure -> embed pipeline CONCURRENTLY
    across many candidates (bounded by MAX_CONCURRENT_PARSES worker threads),
    then applies and commits results sequentially.

    This is the main wall-clock fix for bulk uploads: previously each file was
    processed fully in sequence inside the request (extract, then a blocking
    Claude call, then a blocking Voyage call, one file at a time - the module
    docstring in voyage_client.py flagged this as a known future bottleneck).
    With N candidates and C = MAX_CONCURRENT_PARSES, wall-clock time for the
    AI-bound work goes from roughly N call-latencies to roughly N/C - the
    calls themselves aren't cheaper, but they overlap instead of queueing.

    DB writes are NOT parallelized (a single AsyncSession isn't safe to use
    from multiple concurrent tasks) - they happen in a fast sequential pass
    after all the (slow, network-bound) results are already in hand.
    """
    if not candidates:
        return []

    semaphore = anyio.Semaphore(MAX_CONCURRENT_PARSES)

    async def _bounded(candidate: Candidate) -> PipelineResult:
        async with semaphore:
            return await anyio.to_thread.run_sync(
                _run_extract_and_structure, candidate.file_path, candidate.original_filename
            )

    results = await asyncio.gather(*(_bounded(c) for c in candidates))

    for candidate, result in zip(candidates, results):
        _apply_pipeline_result(candidate, result)
        await _sync_job_embeddings(db, candidate)  # no-op; clears legacy rows only

    await db.commit()
    for candidate in candidates:
        await db.refresh(candidate)
    return candidates


async def reparse_from_stored_text(db: AsyncSession, candidate: Candidate) -> Candidate:
    """Re-runs only the AI structuring step against the raw_text already on
    file - used when the extraction schema changes and files don't need to
    be re-uploaded or re-OCR'd."""
    if not candidate.raw_text or not candidate.raw_text.strip():
        raise ResumeStructuringError("No stored raw text to re-parse from - re-upload the file")

    result = await anyio.to_thread.run_sync(_structure_and_embed, candidate.raw_text)
    _apply_pipeline_result(candidate, result)
    # Matches original semantics: a structuring failure is recorded on the row
    # (parsing_status="failed") and returned normally, not raised - only the
    # missing-raw-text precondition above raises (the route maps that to 400).
    if result.ok:
        await _sync_job_embeddings(db, candidate)  # no-op; clears legacy rows only

    await db.commit()
    await db.refresh(candidate)
    return candidate
