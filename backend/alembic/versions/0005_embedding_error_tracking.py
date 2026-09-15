"""add embedding_error tracking to vacancies and candidates

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-21

"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vacancies", sa.Column("embedding_error", sa.Text(), nullable=True))
    op.add_column("candidates", sa.Column("embedding_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("candidates", "embedding_error")
    op.drop_column("vacancies", "embedding_error")
