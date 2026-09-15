"""phase 4 scoring: wage fields, document_type, responsibilities embedding,
per-job embeddings table

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-24

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 1024  # voyage-multilingual-2


def upgrade() -> None:
    # --- vacancies ---
    op.add_column("vacancies", sa.Column("wage_min", sa.Float(), nullable=True))
    op.add_column("vacancies", sa.Column("wage_max", sa.Float(), nullable=True))
    op.add_column(
        "vacancies",
        sa.Column("wage_currency", sa.String(10), nullable=False, server_default="AZN"),
    )
    op.add_column("vacancies", sa.Column("responsibilities_embedding", Vector(EMBEDDING_DIM), nullable=True))

    # --- candidates ---
    op.add_column("candidates", sa.Column("document_type", sa.String(30), nullable=True))
    op.add_column("candidates", sa.Column("wage_expectation", sa.Float(), nullable=True))
    op.add_column("candidates", sa.Column("wage_currency", sa.String(10), nullable=True))

    # --- per-job embeddings ---
    op.create_table(
        "candidate_job_embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("job_index", sa.Integer(), nullable=False),
        sa.Column("job_title", sa.String(500), nullable=True),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
    )
    op.create_index("ix_candidate_job_embeddings_candidate_id", "candidate_job_embeddings", ["candidate_id"])

    # Existing vacancies carry the pre-Phase-4 weight set (no wage_weight, and
    # sub-weights summing to 1.0 without it). Rewrite them to the new defaults
    # so scoring doesn't silently under-weight everything by the missing 0.10.
    # scoring_weights is a JSON (not JSONB) column, so it's cast for the
    # merge and cast back for storage.
    op.execute(
        """
        UPDATE vacancies
        SET scoring_weights = (
            scoring_weights::jsonb
            || jsonb_build_object(
                'required_skills_weight', 0.45,
                'preferred_skills_weight', 0.15,
                'experience_weight', 0.20,
                'education_weight', 0.10,
                'wage_weight', 0.10
            )
        )::json
        WHERE NOT (scoring_weights::jsonb ? 'wage_weight')
        """
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_job_embeddings_candidate_id", table_name="candidate_job_embeddings")
    op.drop_table("candidate_job_embeddings")

    op.drop_column("candidates", "wage_currency")
    op.drop_column("candidates", "wage_expectation")
    op.drop_column("candidates", "document_type")

    op.drop_column("vacancies", "responsibilities_embedding")
    op.drop_column("vacancies", "wage_currency")
    op.drop_column("vacancies", "wage_max")
    op.drop_column("vacancies", "wage_min")
