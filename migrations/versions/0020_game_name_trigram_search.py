"""add trigram game-name search

Revision ID: 0020
Revises: 0019
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX ix_games_name_trgm ON games USING gin (name gin_trgm_ops)")


def downgrade() -> None:
    op.drop_index("ix_games_name_trgm", table_name="games")
