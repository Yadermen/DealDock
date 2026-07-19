"""region comparison, independent digests and premium audit

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(128), nullable=True))
    op.add_column("users", sa.Column("premium_source", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("comparison_currency", sa.String(3), nullable=False, server_default="USD"))
    op.add_column("users", sa.Column("comparison_regions", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("users", sa.Column("daily_digest_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("daily_digest_time", sa.Time(), nullable=True))
    op.add_column("users", sa.Column("daily_digest_last_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("weekly_digest_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("weekly_digest_time", sa.Time(), nullable=True))
    op.add_column("users", sa.Column("weekly_digest_weekday", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("weekly_digest_last_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        """
        UPDATE users SET
          daily_digest_enabled = digest_mode = 'daily',
          daily_digest_time = CASE WHEN digest_mode = 'daily' THEN digest_time END,
          daily_digest_last_at = CASE WHEN digest_mode = 'daily' THEN last_digest_at END,
          weekly_digest_enabled = digest_mode = 'weekly',
          weekly_digest_time = CASE WHEN digest_mode = 'weekly' THEN digest_time END,
          weekly_digest_weekday = digest_weekday,
          weekly_digest_last_at = CASE WHEN digest_mode = 'weekly' THEN last_digest_at END
        """
    )
    op.create_table(
        "premium_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("admin_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("old_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_premium_audits_user_id", "premium_audits", ["user_id"])
    op.create_index("ix_premium_audits_admin_telegram_id", "premium_audits", ["admin_telegram_id"])
    op.create_index("ix_premium_audits_created_at", "premium_audits", ["created_at"])


def downgrade() -> None:
    op.drop_table("premium_audits")
    for column in (
        "weekly_digest_last_at",
        "weekly_digest_weekday",
        "weekly_digest_time",
        "weekly_digest_enabled",
        "daily_digest_last_at",
        "daily_digest_time",
        "daily_digest_enabled",
        "comparison_regions",
        "comparison_currency",
        "premium_source",
        "display_name",
    ):
        op.drop_column("users", column)
