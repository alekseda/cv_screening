"""add required_skills_language_code / preferred_skills_language_code to vacancies

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-17

"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vacancies",
        sa.Column("required_skills_language_code", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "vacancies",
        sa.Column("preferred_skills_language_code", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("vacancies", "preferred_skills_language_code")
    op.drop_column("vacancies", "required_skills_language_code")
