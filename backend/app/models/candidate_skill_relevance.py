import hashlib
import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def hash_requirement(requirement: str) -> str:
    """Stable cache key for a requirement string - used instead of the raw
    text so the unique index stays small and fixed-width."""
    return hashlib.sha256(requirement.strip().lower().encode("utf-8")).hexdigest()


class CandidateSkillRelevance(Base):
    """Cached AI semantic-fallback judgement for one (candidate, vacancy,
    requirement) triple - see skill_relevance_judge.py.

    Only written for requirements the literal token matcher (skill_matcher.py)
    couldn't confirm; a requirement matched by wording alone never needs this
    fallback and never gets a row here. Keyed by a hash of the requirement
    text rather than a position (unlike CandidateJobRelevance's job_index) -
    a requirement has no stable position, since editing the vacancy can
    reorder or reword requirements, and a hash naturally invalidates when the
    wording changes rather than mis-keying to a different requirement.
    """

    __tablename__ = "candidate_skill_relevance"
    __table_args__ = (
        UniqueConstraint("candidate_id", "vacancy_id", "requirement_hash", name="uq_candidate_vacancy_requirement"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vacancy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vacancies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requirement_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    requirement: Mapped[str] = mapped_column(Text, nullable=False)  # denormalised, for readable debugging
    matched: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
