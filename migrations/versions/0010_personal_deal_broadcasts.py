"""Add durable personalized deal broadcast runs.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "deferred_notifications",
        sa.Column("keyboard_type", sa.String(length=20), nullable=False, server_default="notification"),
    )
    op.create_table(
        "deal_broadcast_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("admin_telegram_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column("status", sa.String(length=24), nullable=False, index=True),
        sa.Column("respect_quiet_hours", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("users_checked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("messages_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("no_deals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deferred", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("temporary_errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("permanent_errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("games_included", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stale_games", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
    )
    op.create_index(
        "uq_active_deal_broadcast",
        "deal_broadcast_runs",
        [sa.text("(1)")],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )
    op.create_table(
        "deal_broadcast_recipients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("deal_broadcast_runs.id", ondelete="CASCADE")),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("status", sa.String(length=24), nullable=False, index=True),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("game_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_pages", sa.JSON(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "user_id", name="uq_deal_broadcast_run_user"),
    )
    op.create_index("ix_deal_broadcast_recipients_run_id", "deal_broadcast_recipients", ["run_id"])
    op.create_index("ix_deal_broadcast_recipients_user_id", "deal_broadcast_recipients", ["user_id"])


def downgrade() -> None:
    op.drop_table("deal_broadcast_recipients")
    op.drop_index("uq_active_deal_broadcast", table_name="deal_broadcast_runs")
    op.drop_table("deal_broadcast_runs")
    op.drop_column("deferred_notifications", "keyboard_type")
