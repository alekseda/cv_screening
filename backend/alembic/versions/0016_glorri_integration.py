"""Glorri.az integration - link fields for vacancy/candidate

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-21

"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vacancies", sa.Column("glorri_job_id", sa.String(length=255), nullable=True))
    op.add_column("candidates", sa.Column("glorri_candidate_id", sa.String(length=255), nullable=True))
    op.create_index("ix_candidates_glorri_candidate_id", "candidates", ["glorri_candidate_id"])


def downgrade() -> None:
    op.drop_index("ix_candidates_glorri_candidate_id", table_name="candidates")
    op.drop_column("candidates", "glorri_candidate_id")
    op.drop_column("vacancies", "glorri_job_id")
