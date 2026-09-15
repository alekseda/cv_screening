"""add experience is_estimate flags and current_year_of_study

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-23

"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidates",
        sa.Column("total_work_experience_is_estimate", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "candidates",
        sa.Column("total_volunteer_experience_is_estimate", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("candidates", sa.Column("current_year_of_study", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("candidates", "current_year_of_study")
    op.drop_column("candidates", "total_volunteer_experience_is_estimate")
    op.drop_column("candidates", "total_work_experience_is_estimate")
