"""add required_skills_type / preferred_skills_type to vacancies

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-17

"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vacancies",
        sa.Column("required_skills_type", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "vacancies",
        sa.Column("preferred_skills_type", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("vacancies", "preferred_skills_type")
    op.drop_column("vacancies", "required_skills_type")
