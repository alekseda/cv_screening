import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.db.base import Base


class Candidate(Base):
    """A parsed resume, scoped to the vacancy it was uploaded against.

    Both `raw_text` (exactly what was extracted from the file) and
    `structured_data` (the AI-parsed JSON) are stored, per the audit
    requirement - if the parsing schema changes later, resumes can be
    re-parsed from raw_text without re-uploading files.
    """

    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vacancy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("vacancies.id", ondelete="CASCADE"), nullable=False)

    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)

    # SHA-256 of the uploaded file's bytes. Lets the SAME resume, uploaded
    # against a second (or third...) vacancy, reuse the first parse's results
    # instead of paying for extraction/structuring/embedding again - see
    # services/parsing/pipeline.apply_cached_parse and the lookup in
    # api/routes/candidates.upload_candidates. Indexed for the lookup; not
    # unique, since the SAME hash legitimately appears once per vacancy a
    # candidate applies to.
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)

    # This candidate's id in Glorri.az, set only when imported via
    # POST /vacancies/{id}/sync-glorri (see services/integrations/glorri_client.py).
    # Null for a manually-uploaded candidate. Indexed so a re-sync can check
    # "have we already imported this Glorri applicant for this vacancy?"
    # without re-creating duplicates.
    glorri_candidate_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)

    raw_text: Mapped[str] = mapped_column(Text, nullable=True)  # extracted before AI structuring
    structured_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # see schemas/resume.py for shape

    detected_language: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "az" | "ru" | "en" | "mixed"
    # Both totals are computed deterministically in Python (experience_calculator.py)
    # from the AI-extracted date ranges, NOT summed by the AI itself - see that
    # module's docstring for why. is_estimate=True means at least one entry lacked
    # usable dates and was excluded from the sum (so the true total may be higher).
    total_work_experience_years: Mapped[float | None] = mapped_column(nullable=True)
    total_work_experience_is_estimate: Mapped[bool] = mapped_column(default=False, nullable=False)
    total_volunteer_experience_years: Mapped[float | None] = mapped_column(nullable=True)
    total_volunteer_experience_is_estimate: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Display-only fields (see ai_structurer.py) - relevant for a small subset of
    # vacancies (e.g. security roles). Deliberately NOT wired into the Phase 4
    # scoring engine by default; only extracted when explicitly stated on the
    # resume, never inferred. Null = not stated on the resume.
    age: Mapped[int | None] = mapped_column(nullable=True)
    date_of_birth: Mapped[str | None] = mapped_column(String(20), nullable=True)  # as stated: YYYY-MM-DD/YYYY-MM/YYYY
    height_cm: Mapped[float | None] = mapped_column(nullable=True)
    military_service: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # "high_school" | "bachelor" | "master" | "phd" | null - highest COMPLETED level.
    # Mirrors Vacancy.education_requirement's tiers for Phase 4 hard-filter matching.
    education_level: Mapped[str | None] = mapped_column(String(30), nullable=True)
    highest_completed_degree_graduation_year: Mapped[int | None] = mapped_column(nullable=True)

    # Enrollment status - a candidate still studying and not yet graduated.
    # Separate from education_level (highest COMPLETED degree) so postings
    # like "final-year student or 2024/2025 graduate" can be matched correctly
    # against a candidate who hasn't finished their degree yet.
    currently_enrolled: Mapped[bool | None] = mapped_column(nullable=True)
    current_degree_level: Mapped[str | None] = mapped_column(String(30), nullable=True)
    current_year_of_study: Mapped[int | None] = mapped_column(nullable=True)
    expected_graduation_year: Mapped[int | None] = mapped_column(nullable=True)

    # Embedding of the candidate's profile text (Phase 3). Null + embedding_error
    # set if the Voyage call failed (e.g. missing API key) - doesn't block
    # parsing success, since the structured data is still useful without it.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.EMBEDDING_DIM), nullable=True)
    embedding_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    parsing_status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # "pending" | "success" | "failed"
    parsing_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # What the uploaded file actually turned out to be. Anything other than
    # "resume" short-circuits scoring to 0 with an explicit reason, rather than
    # silently scoring a national ID or blank page against the requirements.
    document_type: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Entered manually by the recruiter (candidates don't self-serve here yet).
    # Null = not provided, which the scoring engine treats as neutral rather
    # than penalising the candidate for missing data.
    wage_expectation: Mapped[float | None] = mapped_column(nullable=True)
    wage_currency: Mapped[str | None] = mapped_column(String(10), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def full_name(self) -> str | None:
        """Convenience accessor pulling the name out of parsed resume data.
        Exists so list/ranking views (CandidateSummary) can show an actual
        name instead of falling back to the raw filename, without exposing
        the whole structured_data blob in every list response - only
        CandidateRead (the single-candidate detail view) does that."""
        if self.structured_data:
            return self.structured_data.get("full_name")
        return None

    @property
    def email(self) -> str | None:
        """Contact email pulled from parsed resume data, for the ranking view.
        Same rationale as full_name - surface the one field recruiters need
        without shipping the whole structured_data blob. None if the resume
        didn't list one; the frontend renders that as an empty value."""
        if self.structured_data:
            contact = self.structured_data.get("contact") or {}
            return contact.get("email")
        return None

    @property
    def phone(self) -> str | None:
        """Contact phone pulled from parsed resume data, for the ranking view.
        None if the resume didn't list one."""
        if self.structured_data:
            contact = self.structured_data.get("contact") or {}
            return contact.get("phone")
        return None
