"""Add ON DELETE CASCADE to matches/candidates foreign keys

Deleting a candidate that had already been scored raised a foreign-key
violation (surfaced as a 500) because matches.candidate_id was created as a
plain FK in migration 0001, with no ondelete rule and no ORM-side cascade.

The same gap affected vacancy deletion: candidates.vacancy_id and
matches.vacancy_id had no cascade either, so deleting a vacancy that had any
candidates or scores failed the same way.

candidate_job_embeddings and candidate_job_relevances already declare
ondelete="CASCADE" (migrations 0008/0010); this brings the older tables in
line with them.

Constraint names are Postgres's defaults for inline FKs: <table>_<column>_fkey.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-10

"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # matches -> candidates
    op.drop_constraint("matches_candidate_id_fkey", "matches", type_="foreignkey")
    op.create_foreign_key(
        "matches_candidate_id_fkey",
        "matches",
        "candidates",
        ["candidate_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # matches -> vacancies
    op.drop_constraint("matches_vacancy_id_fkey", "matches", type_="foreignkey")
    op.create_foreign_key(
        "matches_vacancy_id_fkey",
        "matches",
        "vacancies",
        ["vacancy_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # candidates -> vacancies
    op.drop_constraint("candidates_vacancy_id_fkey", "candidates", type_="foreignkey")
    op.create_foreign_key(
        "candidates_vacancy_id_fkey",
        "candidates",
        "vacancies",
        ["vacancy_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("candidates_vacancy_id_fkey", "candidates", type_="foreignkey")
    op.create_foreign_key(
        "candidates_vacancy_id_fkey", "candidates", "vacancies", ["vacancy_id"], ["id"]
    )

    op.drop_constraint("matches_vacancy_id_fkey", "matches", type_="foreignkey")
    op.create_foreign_key(
        "matches_vacancy_id_fkey", "matches", "vacancies", ["vacancy_id"], ["id"]
    )

    op.drop_constraint("matches_candidate_id_fkey", "matches", type_="foreignkey")
    op.create_foreign_key(
        "matches_candidate_id_fkey", "matches", "candidates", ["candidate_id"], ["id"]
    )