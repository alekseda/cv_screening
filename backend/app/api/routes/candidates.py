import asyncio
import hashlib
import json
import os
import uuid

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.candidate import Candidate
from app.models.user import User
from app.models.vacancy import Vacancy
from app.schemas.candidate import (
    CandidateRead,
    CandidateSimilarity,
    CandidateSummary,
    CandidateUploadResult,
    CandidateWageUpdate,
    GlorriSyncResult,
    ReparseFailedResult,
)
from app.services.integrations.glorri_client import GlorriError, download_export, download_resume, start_export
from app.services.parsing.ai_structurer import ResumeStructuringError
from app.services.parsing.pipeline import (
    apply_cached_parse,
    parse_and_store_many,
    reparse_from_stored_text,
    save_upload_file,
)
from app.services.scoring.wage_matcher import check_wage

router = APIRouter(tags=["candidates"])

ALLOWED_EXTENSIONS = {"pdf", "docx"}
MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024  # 15 MB per resume - generous for a text-heavy PDF/DOCX


@router.post("/vacancies/{vacancy_id}/candidates", response_model=list[CandidateUploadResult])
async def upload_candidates(
    vacancy_id: uuid.UUID,
    files: list[UploadFile] = File(...),
    wages_json: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Uploads one or more resumes against a vacancy and runs the parsing
    pipeline. Each file's outcome is reported independently, so one bad
    resume doesn't fail the whole batch.

    `wages_json`, if provided, is a JSON array of `{"wage": number|null,
    "currency": string|null}` objects, ONE PER FILE, in the same order as
    `files`. This lets a recruiter record a candidate's wage expectation
    *before* the file is ever parsed - which matters because if that
    expectation is already at or above the vacancy's wage-veto threshold
    (see wage_matcher.HARD_FAIL_MULTIPLE), there is no point spending the
    parsing/embedding/relevance-judgement calls on that resume at all: the
    candidate will score 0 regardless of anything else. Such a candidate's
    file is still stored (for the audit trail) but never sent through the AI
    pipeline - parsing_status is set to "skipped_wage_veto" instead.

    EFFICIENCY PASS (cost + time, no accuracy change):
      - Content-hash reuse: each file's bytes are hashed (SHA-256). If an
        identical file has already been successfully parsed anywhere in the
        system (e.g. the same candidate applied to a different vacancy), that
        parse's results are copied onto the new row and the AI
        pipeline is skipped for it entirely - status "cached" in the response.
      - Concurrent processing: files that DO need the full pipeline are no
        longer run one at a time inside this request. They're batched through
        parse_and_store_many, which runs the blocking extract/structure/embed
        work for all of them concurrently (bounded by MAX_CONCURRENT_PARSES
        worker threads) - see that function's docstring for the wall-clock
        math. For a few hundred resumes this is the difference between
        minutes and tens of minutes.
    """
    vacancy = await db.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vacancy not found")

    wages: list[dict] = []
    if wages_json:
        try:
            parsed = json.loads(wages_json)
            if isinstance(parsed, list):
                wages = parsed
        except (json.JSONDecodeError, TypeError):
            wages = []  # malformed input shouldn't block the upload - just treat as "no wages given"

    # results[i] corresponds to files[i]; filled in as each file is resolved
    # (rejected / cached / skipped-by-wage / queued-for-pipeline).
    results: list[CandidateUploadResult | None] = [None] * len(files)
    # (position_in_files, candidate) pairs still needing the full AI pipeline.
    to_process: list[tuple[int, Candidate]] = []

    for index, upload in enumerate(files):
        filename = upload.filename or "unnamed"
        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

        if ext not in ALLOWED_EXTENSIONS:
            results[index] = CandidateUploadResult(
                filename=filename,
                candidate_id=None,
                status="rejected",
                error=f"Unsupported file type .{ext} - only .pdf and .docx are accepted",
            )
            continue

        content = await upload.read()
        if len(content) > MAX_FILE_SIZE_BYTES:
            results[index] = CandidateUploadResult(
                filename=filename,
                candidate_id=None,
                status="rejected",
                error=f"File exceeds the {MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB limit",
            )
            continue

        wage_info = wages[index] if index < len(wages) and isinstance(wages[index], dict) else {}
        wage_expectation = wage_info.get("wage")
        wage_currency = wage_info.get("currency")
        try:
            wage_expectation = float(wage_expectation) if wage_expectation is not None else None
        except (TypeError, ValueError):
            wage_expectation = None

        file_hash = hashlib.sha256(content).hexdigest()

        candidate = Candidate(
            vacancy_id=vacancy_id,
            original_filename=filename,
            file_path="",  # filled in below once we have the candidate id
            file_hash=file_hash,
            parsing_status="pending",
            wage_expectation=wage_expectation,
            wage_currency=wage_currency,
        )
        db.add(candidate)
        await db.flush()  # assigns candidate.id without committing yet

        candidate.file_path = save_upload_file(vacancy_id, candidate.id, filename, content)
        await db.commit()
        await db.refresh(candidate)

        # Wage prescreen: if the recruiter already knows this candidate wants
        # at least double the vacancy's budget, skip the entire AI pipeline -
        # text extraction, structuring, embeddings, relevance judgement - none
        # of it can change a result the wage alone already determines. Reuses
        # the exact same threshold as the scoring engine's own wage veto, so
        # the two can never disagree.
        wage_check = check_wage(wage_expectation, wage_currency, vacancy)
        if wage_check.get("hard_fail"):
            candidate.parsing_status = "skipped_wage_veto"
            candidate.parsing_error = wage_check["reason"]
            await db.commit()
            await db.refresh(candidate)
            results[index] = CandidateUploadResult(
                filename=filename,
                candidate_id=candidate.id,
                status="skipped",
                error=wage_check["reason"],
            )
            continue

        # Content-hash reuse: has this exact file already been successfully
        # parsed anywhere (any vacancy)? If so, copy those results instead of
        # re-running extraction/structuring/embedding.
        cached_source = (
            await db.execute(
                select(Candidate)
                .where(
                    Candidate.file_hash == file_hash,
                    Candidate.parsing_status == "success",
                    Candidate.id != candidate.id,
                )
                .order_by(Candidate.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if cached_source is not None:
            apply_cached_parse(candidate, cached_source)
            await db.commit()
            await db.refresh(candidate)
            results[index] = CandidateUploadResult(
                filename=filename,
                candidate_id=candidate.id,
                status="cached",
                error=None,
            )
            continue

        to_process.append((index, candidate))

    if to_process:
        candidates_to_process = [c for _, c in to_process]
        processed = await parse_and_store_many(db, candidates_to_process)
        for (index, _), candidate in zip(to_process, processed):
            results[index] = CandidateUploadResult(
                filename=candidate.original_filename,
                candidate_id=candidate.id,
                status=candidate.parsing_status,
                error=candidate.parsing_error,
            )

    return results


@router.post("/vacancies/{vacancy_id}/sync-glorri", response_model=GlorriSyncResult)
async def sync_glorri_applicants(
    vacancy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Pulls this vacancy's fresh applicants from Glorri.az and runs them
    through the same upload/parse pipeline as a manual file upload.

    Only Glorri's APPLIED stage is imported - candidates already moved to
    SHORTLISTED/LEFT/etc. in Glorri have already been triaged there, not
    what a fresh sync is for. Company-wide blacklisted candidates (by email)
    are always excluded. Re-running this is safe: candidates already
    imported in a previous sync (matched by Glorri's own candidate id) are
    skipped, not duplicated. A candidate with no resumeUrl can't be parsed
    and is reported rather than imported.

    Requires `Vacancy.glorri_job_id` to be set first (PATCH the vacancy).
    """
    vacancy = await db.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vacancy not found")
    if not vacancy.glorri_job_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This vacancy has no glorri_job_id set - PATCH the vacancy with one before syncing",
        )

    try:
        export_meta = await anyio.to_thread.run_sync(start_export)
        export_data = await anyio.to_thread.run_sync(download_export, export_meta["downloadUrl"])
    except GlorriError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Glorri sync failed: {e}") from e

    job = next(
        (j for j in export_data.get("jobs") or [] if j.get("id") == vacancy.glorri_job_id),
        None,
    )
    if job is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No job with id {vacancy.glorri_job_id!r} found in the current Glorri export - "
            "check the vacancy's glorri_job_id",
        )

    blacklisted_emails = {
        email.strip().lower()
        for entry in (export_data.get("blacklistCandidates") or [])
        for email in (entry.get("emails") or [])
        if email
    }

    applied_candidates = [
        gc
        for stage in job.get("stages") or []
        if stage.get("name") == "APPLIED"
        for gc in stage.get("candidates") or []
    ]

    already_imported = {
        row[0]
        for row in (
            await db.execute(
                select(Candidate.glorri_candidate_id).where(
                    Candidate.vacancy_id == vacancy_id,
                    Candidate.glorri_candidate_id.isnot(None),
                )
            )
        ).all()
    }

    skipped_duplicate = 0
    skipped_blacklisted = 0
    skipped_no_resume: list[str] = []
    failed: list[dict] = []

    # Phase 1: cheap, sequential, no I/O - decide which applicants are even
    # worth downloading a résumé for.
    to_download: list[tuple[dict, str, str]] = []  # (glorri_candidate, display_name, resume_url)
    for gc in applied_candidates:
        glorri_id = gc.get("id")
        name_parts = gc.get("name") or {}
        display_name = f"{name_parts.get('first', '')} {name_parts.get('last', '')}".strip() or glorri_id or "unknown"

        if glorri_id and glorri_id in already_imported:
            skipped_duplicate += 1
            continue

        email = (gc.get("email") or "").strip().lower()
        if email and email in blacklisted_emails:
            skipped_blacklisted += 1
            continue

        resume_url = gc.get("resumeUrl")
        if not resume_url:
            skipped_no_resume.append(display_name)
            continue

        to_download.append((gc, display_name, resume_url))

    # Phase 2: the slow part - downloading each résumé file from wherever
    # Glorri stored it. These are independent network calls with no DB
    # access, so they run concurrently, bounded by
    # MAX_CONCURRENT_GLORRI_DOWNLOADS (same "gather the slow work
    # concurrently, then write sequentially" shape as parse_and_store_many
    # and score_vacancy_candidates use, and for the same reason: a single
    # AsyncSession isn't safe to use from multiple concurrent tasks).
    semaphore = anyio.Semaphore(settings.MAX_CONCURRENT_GLORRI_DOWNLOADS)

    async def _bounded_download(resume_url: str) -> tuple[bytes, str] | GlorriError:
        async with semaphore:
            try:
                return await anyio.to_thread.run_sync(download_resume, resume_url)
            except GlorriError as e:
                return e

    downloads = await asyncio.gather(*(_bounded_download(url) for _, _, url in to_download))

    # Phase 3: sequential again - validate each download and write to the DB.
    to_process: list[Candidate] = []
    imported_via_cache = 0

    for (glorri_candidate, display_name, _), outcome in zip(to_download, downloads):
        if isinstance(outcome, GlorriError):
            failed.append({"name": display_name, "error": str(outcome)})
            continue
        content, filename = outcome

        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        if ext not in ALLOWED_EXTENSIONS:
            failed.append({"name": display_name, "error": f"Unsupported resume file type: {filename}"})
            continue
        if len(content) > MAX_FILE_SIZE_BYTES:
            failed.append({"name": display_name, "error": "Resume file exceeds the size limit"})
            continue

        file_hash = hashlib.sha256(content).hexdigest()
        candidate = Candidate(
            vacancy_id=vacancy_id,
            original_filename=filename,
            file_path="",
            file_hash=file_hash,
            parsing_status="pending",
            glorri_candidate_id=glorri_candidate.get("id"),
        )
        db.add(candidate)
        await db.flush()

        candidate.file_path = save_upload_file(vacancy_id, candidate.id, filename, content)
        await db.commit()
        await db.refresh(candidate)

        # Same content-hash reuse as a manual upload - see upload_candidates.
        cached_source = (
            await db.execute(
                select(Candidate)
                .where(
                    Candidate.file_hash == file_hash,
                    Candidate.parsing_status == "success",
                    Candidate.id != candidate.id,
                )
                .order_by(Candidate.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if cached_source is not None:
            apply_cached_parse(candidate, cached_source)
            await db.commit()
            imported_via_cache += 1
            continue

        to_process.append(candidate)

    if to_process:
        await parse_and_store_many(db, to_process)

    return GlorriSyncResult(
        imported=imported_via_cache + len(to_process),
        skipped_duplicate=skipped_duplicate,
        skipped_blacklisted=skipped_blacklisted,
        skipped_no_resume=skipped_no_resume,
        failed=failed,
    )


@router.get("/vacancies/{vacancy_id}/candidates", response_model=list[CandidateSummary])
async def list_candidates(
    vacancy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    vacancy = await db.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vacancy not found")

    result = await db.execute(
        select(Candidate).where(Candidate.vacancy_id == vacancy_id).order_by(Candidate.created_at.desc())
    )
    return result.scalars().all()


@router.get("/vacancies/{vacancy_id}/candidates/by-similarity", response_model=list[CandidateSimilarity])
async def list_candidates_by_similarity(
    vacancy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Phase 3 preview: candidates for this vacancy ranked by semantic
    (embedding) similarity ALONE. This is NOT the final match score - Phase 4
    will blend this with rule-based hard filters (skills/experience/education).
    Useful now to sanity-check that embeddings are being generated and are
    actually distinguishing relevant from irrelevant resumes."""
    vacancy = await db.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vacancy not found")
    if vacancy.embedding is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This vacancy has no embedding yet - see embedding_error on the vacancy, "
            "or try POST /vacancies/{id}/recompute-embedding",
        )

    distance = Candidate.embedding.cosine_distance(vacancy.embedding)
    stmt = (
        select(Candidate, distance.label("distance"))
        .where(Candidate.vacancy_id == vacancy_id, Candidate.embedding.is_not(None))
        .order_by(distance)
    )
    rows = (await db.execute(stmt)).all()

    return [
        CandidateSimilarity(
            candidate=CandidateSummary.model_validate(candidate),
            semantic_score=round((1 - cosine_distance) * 100, 1),
        )
        for candidate, cosine_distance in rows
    ]


@router.get("/candidates/{candidate_id}", response_model=CandidateRead)
async def get_candidate(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    candidate = await db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found")
    return candidate


@router.delete("/candidates/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_candidate(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Hard delete - permanently removes the DB row (raw_text, structured_data,
    parsing history) and the stored resume file from disk. Not recoverable."""
    candidate = await db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found")

    file_path = candidate.file_path
    await db.delete(candidate)
    await db.commit()

    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError:
            # The DB row is already gone - a leftover file on disk is a cleanup
            # issue, not a reason to fail the request or roll back the delete.
            pass


@router.post("/candidates/{candidate_id}/reparse", response_model=CandidateRead)
async def reparse_candidate(
    candidate_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Re-runs AI structuring against the raw_text already stored - no file
    re-upload or re-OCR needed. Use this after changing the extraction schema
    or prompt."""
    candidate = await db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found")

    try:
        candidate = await reparse_from_stored_text(db, candidate)
    except ResumeStructuringError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    return candidate


@router.post("/vacancies/{vacancy_id}/candidates/reparse-failed", response_model=ReparseFailedResult)
async def reparse_failed_candidates(
    vacancy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retry AI structuring for every candidate in this vacancy whose parse
    failed, reusing the raw_text already on file - no re-upload, no re-OCR,
    and none of the (paid) text extraction work is repeated.

    Deliberately best-effort per candidate: one resume that fails again does
    not abort the run, since the common trigger for this endpoint is a
    transient/quota-related outage that hit an arbitrary subset of a batch.
    Failures stay recorded on their own rows with parsing_error, so the
    recruiter can see which ones are still broken.
    """
    vacancy = await db.get(Vacancy, vacancy_id)
    if vacancy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vacancy not found")

    failed = (
        await db.execute(
            select(Candidate).where(
                Candidate.vacancy_id == vacancy_id,
                Candidate.parsing_status == "failed",
            )
        )
    ).scalars().all()

    recovered = 0
    still_failed = 0
    needs_reupload = 0

    for candidate in failed:
        if not candidate.raw_text or not candidate.raw_text.strip():
            # Extraction itself never produced text - re-structuring has
            # nothing to work from. Counted separately so the UI can tell the
            # recruiter "retry won't help, re-upload this one".
            needs_reupload += 1
            continue
        try:
            refreshed = await reparse_from_stored_text(db, candidate)
        except ResumeStructuringError:
            still_failed += 1
            continue
        if refreshed.parsing_status == "success":
            recovered += 1
        else:
            still_failed += 1

    return ReparseFailedResult(
        attempted=len(failed),
        recovered=recovered,
        still_failed=still_failed,
        needs_reupload=needs_reupload,
    )


@router.patch("/candidates/{candidate_id}/wage", response_model=CandidateRead)
async def set_candidate_wage(
    candidate_id: uuid.UUID,
    payload: CandidateWageUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Records the candidate's salary expectation, entered by the recruiter.

    Kept separate from the upload flow because wage expectations usually
    arrive outside the CV (a phone screen, an application form) rather than
    in the document itself. Re-score afterwards to reflect it:
    POST /vacancies/{vacancy_id}/candidates/{candidate_id}/score
    """
    candidate = await db.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate not found")

    candidate.wage_expectation = payload.wage_expectation
    candidate.wage_currency = payload.wage_currency
    await db.commit()
    await db.refresh(candidate)
    return candidate
