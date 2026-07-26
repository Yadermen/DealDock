"""add cached external price history

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "price_history_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="isthereanydeal"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_price_history_cache_game_id", "price_history_cache", ["game_id"])
    op.create_index("ix_price_history_cache_updated_at", "price_history_cache", ["updated_at"])
    op.create_index(
        "uq_price_history_game_country",
        "price_history_cache",
        ["game_id", "country_code"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_price_history_game_country", table_name="price_history_cache")
    op.drop_index("ix_price_history_cache_updated_at", table_name="price_history_cache")
    op.drop_index("ix_price_history_cache_game_id", table_name="price_history_cache")
    op.drop_table("price_history_cache")
