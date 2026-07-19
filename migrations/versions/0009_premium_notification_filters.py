"""Add Premium notification filters and deferred Steam links.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "watch_rules",
        sa.Column("notify_on_new_historical_low", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "watch_rules",
        sa.Column("notify_on_known_historical_low", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("watch_rules", sa.Column("minimum_price_drop_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column("watch_rules", sa.Column("minimum_price_drop_percent", sa.Integer(), nullable=True))
    op.add_column(
        "watch_rules", sa.Column("notify_on_any_price_drop", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column(
        "watch_rules",
        sa.Column("repeat_notification_policy", sa.String(length=20), nullable=False, server_default="on_change"),
    )
    op.add_column("deferred_notifications", sa.Column("steam_app_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE deferred_notifications d SET steam_app_id = g.steam_app_id "
        "FROM games g WHERE d.game_id = g.id AND d.steam_app_id IS NULL"
    )


def downgrade() -> None:
    op.drop_column("deferred_notifications", "steam_app_id")
    op.drop_column("watch_rules", "repeat_notification_policy")
    op.drop_column("watch_rules", "notify_on_any_price_drop")
    op.drop_column("watch_rules", "minimum_price_drop_percent")
    op.drop_column("watch_rules", "minimum_price_drop_amount")
    op.drop_column("watch_rules", "notify_on_known_historical_low")
    op.drop_column("watch_rules", "notify_on_new_historical_low")
