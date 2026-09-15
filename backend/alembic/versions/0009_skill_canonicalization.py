"""add required_skills_canonical / preferred_skills_canonical to vacancies

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-24

"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vacancies",
        sa.Column("required_skills_canonical", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "vacancies",
        sa.Column("preferred_skills_canonical", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("vacancies", "preferred_skills_canonical")
    op.drop_column("vacancies", "required_skills_canonical")
