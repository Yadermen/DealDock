"""premium lifecycle, notification deduplication and delivery preferences

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("premium_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("premium_expired_notified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("quiet_hours_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("quiet_hours_start", sa.Time(), nullable=True))
    op.add_column("users", sa.Column("quiet_hours_end", sa.Time(), nullable=True))
    op.add_column("users", sa.Column("digest_mode", sa.String(10), nullable=False, server_default="off"))
    op.add_column("users", sa.Column("digest_time", sa.Time(), nullable=True))
    op.add_column("users", sa.Column("digest_weekday", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("last_digest_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE users SET premium_started_at = created_at WHERE plan = 'PREMIUM'")

    op.add_column("watch_rules", sa.Column("last_notified_discount", sa.Integer(), nullable=True))
    op.add_column("watch_rules", sa.Column("last_notification_type", sa.String(30), nullable=True))
    op.add_column("watch_rules", sa.Column("last_notification_fingerprint", sa.String(128), nullable=True))
    op.add_column("watch_rules", sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "watch_rules", sa.Column("condition_was_met", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("payments", sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table(
        "deferred_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=True),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("notification_type", sa.String(30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("send_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_deferred_notifications_user_id", "deferred_notifications", ["user_id"])
    op.create_index("ix_deferred_notifications_game_id", "deferred_notifications", ["game_id"])
    op.create_index("ix_deferred_notifications_send_after", "deferred_notifications", ["send_after"])
    op.create_index("ix_deferred_notifications_fingerprint", "deferred_notifications", ["fingerprint"])


def downgrade() -> None:
    op.drop_table("deferred_notifications")
    op.drop_column("payments", "is_test")
    for column in (
        "condition_was_met",
        "last_notified_at",
        "last_notification_fingerprint",
        "last_notification_type",
        "last_notified_discount",
    ):
        op.drop_column("watch_rules", column)
    for column in (
        "last_digest_at",
        "digest_weekday",
        "digest_time",
        "digest_mode",
        "quiet_hours_end",
        "quiet_hours_start",
        "quiet_hours_enabled",
        "premium_expired_notified_at",
        "premium_started_at",
    ):
        op.drop_column("users", column)
