"""add persistent currency rate cache

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "currency_rate_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("base_currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("rates", sa.JSON(), nullable=False),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("currency_rate_cache")
