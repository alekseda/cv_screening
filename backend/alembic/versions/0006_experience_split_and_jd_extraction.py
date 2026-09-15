"""candidate work/volunteer experience split + enrollment status;
vacancy responsibilities + enrollment-acceptance + extraction_error

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-23

"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- candidates ---
    op.alter_column("candidates", "total_experience_years", new_column_name="total_work_experience_years")
    op.add_column("candidates", sa.Column("total_volunteer_experience_years", sa.Float(), nullable=True))
    op.add_column("candidates", sa.Column("highest_completed_degree_graduation_year", sa.Integer(), nullable=True))
    op.add_column("candidates", sa.Column("currently_enrolled", sa.Boolean(), nullable=True))
    op.add_column("candidates", sa.Column("current_degree_level", sa.String(30), nullable=True))
    op.add_column("candidates", sa.Column("expected_graduation_year", sa.Integer(), nullable=True))

    # --- vacancies ---
    op.add_column("vacancies", sa.Column("responsibilities", sa.Text(), nullable=True))
    op.add_column(
        "vacancies",
        sa.Column("accepts_current_students", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("vacancies", sa.Column("graduation_year_min", sa.Integer(), nullable=True))
    op.add_column("vacancies", sa.Column("graduation_year_max", sa.Integer(), nullable=True))
    op.add_column("vacancies", sa.Column("extraction_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("vacancies", "extraction_error")
    op.drop_column("vacancies", "graduation_year_max")
    op.drop_column("vacancies", "graduation_year_min")
    op.drop_column("vacancies", "accepts_current_students")
    op.drop_column("vacancies", "responsibilities")

    op.drop_column("candidates", "expected_graduation_year")
    op.drop_column("candidates", "current_degree_level")
    op.drop_column("candidates", "currently_enrolled")
    op.drop_column("candidates", "highest_completed_degree_graduation_year")
    op.drop_column("candidates", "total_volunteer_experience_years")
    op.alter_column("candidates", "total_work_experience_years", new_column_name="total_experience_years")
