import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.db.base import Base


class CandidateJobEmbedding(Base):
    """One embedding per work_history entry, used to weight that job's
    duration by how relevant it is to a given vacancy.

    Why a child table rather than a column on Candidate: work_history is a
    variable-length list, and pgvector columns are fixed-shape - one row per
    job is the clean way to store N embeddings for N jobs.

    What gets embedded (`source_text`) is the job's `description` when it has
    one, falling back to its `title` when the description is blank. This is
    compared against the vacancy's *responsibilities* embedding rather than
    its full description: what a role involves day-to-day is the meaningful
    comparison for "has this person done work like this", whereas titles alone
    are unreliable (two "Manager" roles can be entirely different jobs).

    `source_text` is stored alongside the vector so a recruiter (or a future
    debugging session) can see exactly what text produced a relevance score.
    """

    __tablename__ = "candidate_job_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Index into the candidate's structured_data["work_history"] array, so a
    # relevance score can be traced back to the specific job it came from.
    job_index: Mapped[int] = mapped_column(Integer, nullable=False)
    job_title: Mapped[str | None] = mapped_column(String(500), nullable=True)  # denormalised for readable breakdowns
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.EMBEDDING_DIM), nullable=True)
