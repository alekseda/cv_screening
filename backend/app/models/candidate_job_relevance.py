import uuid

from sqlalchemy import Float, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CandidateJobRelevance(Base):
    """Cached AI relevance judgement for one (candidate, vacancy, job) triple.

    Relevance depends on both the job and the vacancy, so it can't live on the
    candidate alone. Caching it here means the (paid) judgement call happens
    once per candidate per vacancy: re-running scoring after tuning weights
    reuses these rows and costs nothing.

    `reason` is the model's one-line explanation, surfaced in the score
    breakdown so a recruiter can see why a job was or wasn't counted.
    """

    __tablename__ = "candidate_job_relevance"
    __table_args__ = (
        UniqueConstraint("candidate_id", "vacancy_id", "job_index", name="uq_candidate_vacancy_job"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vacancy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vacancies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_index: Mapped[int] = mapped_column(Integer, nullable=False)
    relevance: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
