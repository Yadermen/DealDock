"""add external historical lows

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_historical_lows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("shop_name", sa.String(length=100), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_external_historical_lows_game_id", "external_historical_lows", ["game_id"])
    op.create_index("ix_external_historical_lows_updated_at", "external_historical_lows", ["updated_at"])
    op.create_index(
        "uq_external_low_game_country_scope",
        "external_historical_lows",
        ["game_id", "country_code", "scope"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("external_historical_lows")
