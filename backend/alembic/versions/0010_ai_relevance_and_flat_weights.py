"""AI-judged relevance cache + flat six-component scoring weights

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-24

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_job_relevance",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "vacancy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vacancies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("job_index", sa.Integer(), nullable=False),
        sa.Column("relevance", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("candidate_id", "vacancy_id", "job_index", name="uq_candidate_vacancy_job"),
    )
    op.create_index("ix_candidate_job_relevance_candidate_id", "candidate_job_relevance", ["candidate_id"])
    op.create_index("ix_candidate_job_relevance_vacancy_id", "candidate_job_relevance", ["vacancy_id"])

    # Scoring moved from a two-tier (hard filter vs semantic) model to a flat
    # six-component weighted sum. Existing vacancies carry the old keys, whose
    # sub-weights sum to 1.0 WITHOUT responsibilities_weight - leaving them in
    # place would drop the new 0.45 component entirely. Rewrite every vacancy
    # to the new defaults; recruiters who had tuned weights will need to retune
    # against the new component set.
    op.execute(
        """
        UPDATE vacancies
        SET scoring_weights = jsonb_build_object(
            'responsibilities_weight', 0.45,
            'wage_weight', 0.15,
            'experience_weight', 0.15,
            'required_skills_weight', 0.10,
            'education_weight', 0.10,
            'preferred_skills_weight', 0.05
        )::json
        """
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_job_relevance_vacancy_id", table_name="candidate_job_relevance")
    op.drop_index("ix_candidate_job_relevance_candidate_id", table_name="candidate_job_relevance")
    op.drop_table("candidate_job_relevance")

    op.execute(
        """
        UPDATE vacancies
        SET scoring_weights = jsonb_build_object(
            'hard_filter_weight', 0.575,
            'semantic_weight', 0.425,
            'required_skills_weight', 0.45,
            'preferred_skills_weight', 0.15,
            'experience_weight', 0.20,
            'education_weight', 0.10,
            'wage_weight', 0.10
        )::json
        """
    )
