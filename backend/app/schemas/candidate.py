import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CandidateSummary(BaseModel):
    """Lightweight view for list endpoints - no raw_text/structured_data payload."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str
    full_name: str | None
    email: str | None
    phone: str | None
    parsing_status: str
    parsing_error: str | None
    detected_language: str | None
    total_work_experience_years: float | None
    total_work_experience_is_estimate: bool
    total_volunteer_experience_years: float | None
    total_volunteer_experience_is_estimate: bool
    age: int | None
    date_of_birth: str | None
    height_cm: float | None
    education_level: str | None
    highest_completed_degree_graduation_year: int | None
    currently_enrolled: bool | None
    current_degree_level: str | None
    current_year_of_study: int | None
    expected_graduation_year: int | None
    military_service: str | None
    document_type: str | None
    wage_expectation: float | None
    wage_currency: str | None
    embedding_error: str | None
    created_at: datetime


class CandidateRead(CandidateSummary):
    """Full view - includes the structured JSON and raw extracted text,
    per the audit requirement."""

    structured_data: dict | None
    raw_text: str | None


class CandidateUploadResult(BaseModel):
    """One entry per file in a batch upload response - lets the frontend show
    per-file success/failure rather than a single pass/fail for the whole batch."""

    filename: str
    candidate_id: uuid.UUID | None
    status: str  # "success" | "failed" | "rejected"
    error: str | None = None


class CandidateSimilarity(BaseModel):
    """Phase 3 preview - semantic similarity ONLY, not the final Phase 4 score
    (which will blend this with hard filters on skills/experience/education)."""

    candidate: CandidateSummary
    semantic_score: float  # 0-100, cosine similarity scaled


class CandidateWageUpdate(BaseModel):
    """Recruiter-entered salary expectation. Kept as its own narrow endpoint
    rather than a general per-field override mechanism - the latter is a
    reasonable future addition but broader than what wage entry needs."""

    wage_expectation: float | None = Field(default=None, ge=0)
    wage_currency: str | None = Field(default="AZN", max_length=10)


class GlorriSyncResult(BaseModel):
    """Outcome of POST /vacancies/{id}/sync-glorri - pulling this vacancy's
    APPLIED-stage applicants from Glorri.az and running them through the
    normal upload/parse pipeline."""

    imported: int
    skipped_duplicate: int  # already imported in a previous sync
    skipped_blacklisted: int  # on Glorri's company-wide blacklist by email
    skipped_no_resume: list[str]  # applicant names with no resumeUrl to parse
    failed: list[dict]  # [{"name": ..., "error": ...}] - download/type/size failures


class ReparseFailedResult(BaseModel):
    """Outcome of a bulk retry across every failed candidate in a vacancy.

    `needs_reupload` counts rows whose text extraction never produced anything
    (corrupt file, image-only PDF that OCR couldn't read) - there is no stored
    raw_text to re-structure, so retrying can't help and the file has to be
    uploaded again.
    """

    attempted: int
    recovered: int
    still_failed: int
    needs_reupload: int
