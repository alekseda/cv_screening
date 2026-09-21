"""Semantic fallback cache for skill requirement matching

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-21

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_skill_relevance",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "vacancy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vacancies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requirement_hash", sa.String(length=64), nullable=False),
        sa.Column("requirement", sa.Text(), nullable=False),
        sa.Column("matched", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("candidate_id", "vacancy_id", "requirement_hash", name="uq_candidate_vacancy_requirement"),
    )
    op.create_index("ix_candidate_skill_relevance_candidate_id", "candidate_skill_relevance", ["candidate_id"])
    op.create_index("ix_candidate_skill_relevance_vacancy_id", "candidate_skill_relevance", ["vacancy_id"])


def downgrade() -> None:
    op.drop_index("ix_candidate_skill_relevance_vacancy_id", table_name="candidate_skill_relevance")
    op.drop_index("ix_candidate_skill_relevance_candidate_id", table_name="candidate_skill_relevance")
    op.drop_table("candidate_skill_relevance")
