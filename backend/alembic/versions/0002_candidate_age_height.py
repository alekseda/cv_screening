"""add age and height_cm to candidates

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-21

"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("candidates", sa.Column("age", sa.Integer(), nullable=True))
    op.add_column("candidates", sa.Column("height_cm", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("candidates", "height_cm")
    op.drop_column("candidates", "age")
