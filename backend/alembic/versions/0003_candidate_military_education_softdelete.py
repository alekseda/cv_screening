"""add military_service, education_level, soft delete to candidates

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-21

"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("candidates", sa.Column("military_service", sa.String(30), nullable=True))
    op.add_column("candidates", sa.Column("education_level", sa.String(30), nullable=True))
    op.add_column(
        "candidates",
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("candidates", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_candidates_is_deleted", "candidates", ["is_deleted"])


def downgrade() -> None:
    op.drop_index("ix_candidates_is_deleted", table_name="candidates")
    op.drop_column("candidates", "deleted_at")
    op.drop_column("candidates", "is_deleted")
    op.drop_column("candidates", "education_level")
    op.drop_column("candidates", "military_service")
