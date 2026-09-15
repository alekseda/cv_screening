"""initial schema: users, vacancies, candidates, matches

Revision ID: 0001
Revises:
Create Date: 2026-07-15

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

EMBEDDING_DIM = 1024  # voyage-multilingual-2


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "vacancies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("required_skills", sa.JSON, nullable=False),
        sa.Column("preferred_skills", sa.JSON, nullable=False),
        sa.Column("min_experience_years", sa.Float, nullable=False, server_default="0"),
        sa.Column("education_requirement", sa.String(50), nullable=False, server_default="none"),
        sa.Column("scoring_weights", sa.JSON, nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("vacancy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vacancies.id"), nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("file_path", sa.String(1000), nullable=False),
        sa.Column("raw_text", sa.Text, nullable=True),
        sa.Column("structured_data", sa.JSON, nullable=True),
        sa.Column("detected_language", sa.String(10), nullable=True),
        sa.Column("total_experience_years", sa.Float, nullable=True),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("parsing_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("parsing_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_candidates_vacancy_id", "candidates", ["vacancy_id"])

    op.create_table(
        "matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("candidates.id"), nullable=False),
        sa.Column("vacancy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vacancies.id"), nullable=False),
        sa.Column("total_score", sa.Float, nullable=False),
        sa.Column("hard_filter_score", sa.Float, nullable=False),
        sa.Column("semantic_score", sa.Float, nullable=False),
        sa.Column("score_breakdown", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_matches_vacancy_id", "matches", ["vacancy_id"])
    op.create_index("ix_matches_candidate_id", "matches", ["candidate_id"])


def downgrade() -> None:
    op.drop_table("matches")
    op.drop_table("candidates")
    op.drop_table("vacancies")
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS vector")
