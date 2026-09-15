import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Match(Base):
    """A scored candidate/vacancy pair. Stored (not just computed on the fly)
    so recruiters can revisit results and so weight changes can be re-run
    without re-parsing/re-embedding.

    `score_breakdown` is the auditability contract - it must always contain
    enough detail to answer "why did this candidate get this score", e.g.:
    {
      "matched_required_skills": [...], "missing_required_skills": [...],
      "matched_preferred_skills": [...], "missing_preferred_skills": [...],
      "experience_check": {"required": 3, "candidate": 4.5, "passed": true},
      "education_check": {"required": "bachelor", "candidate": "master", "passed": true},
      "semantic_similarity_raw": 0.71,
      "weights_used": {...}
    }
    """

    __tablename__ = "matches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False)
    vacancy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("vacancies.id", ondelete="CASCADE"), nullable=False)

    total_score: Mapped[float] = mapped_column(Float, nullable=False)      # 0-100
    hard_filter_score: Mapped[float] = mapped_column(Float, nullable=False)  # 0-100
    semantic_score: Mapped[float] = mapped_column(Float, nullable=False)     # 0-100

    score_breakdown: Mapped[dict] = mapped_column(JSON, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
