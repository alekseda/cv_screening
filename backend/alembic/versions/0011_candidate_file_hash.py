"""Candidate content-hash column, for cross-vacancy parse reuse (efficiency pass)

Adds candidates.file_hash (SHA-256 of the uploaded file's bytes) so the
upload route can detect when an identical resume has already been parsed
under a different vacancy and skip extraction/structuring/embedding for it -
see services/parsing/pipeline.apply_cached_parse.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-30

"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("candidates", sa.Column("file_hash", sa.String(length=64), nullable=True))
    op.create_index("ix_candidates_file_hash", "candidates", ["file_hash"])


def downgrade() -> None:
    op.drop_index("ix_candidates_file_hash", table_name="candidates")
    op.drop_column("candidates", "file_hash")
