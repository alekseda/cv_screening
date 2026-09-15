"""switch candidate delete to hard delete, add date_of_birth

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-21

"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Candidate delete is now a hard delete (removes the row + file entirely),
    # so the soft-delete bookkeeping columns from 0003 are no longer used.
    op.drop_index("ix_candidates_is_deleted", table_name="candidates")
    op.drop_column("candidates", "deleted_at")
    op.drop_column("candidates", "is_deleted")

    op.add_column("candidates", sa.Column("date_of_birth", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("candidates", "date_of_birth")

    op.add_column(
        "candidates",
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("candidates", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_candidates_is_deleted", "candidates", ["is_deleted"])
