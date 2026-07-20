"""add giveaway notification preferences and deduplication

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("giveaway_notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "users",
        sa.Column(
            "giveaway_notification_kinds",
            sa.JSON(),
            nullable=False,
            server_default='["keep", "weekend", "dlc"]',
        ),
    )
    op.create_table(
        "giveaway_notification_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("giveaway_id", sa.Integer(), sa.ForeignKey("giveaways.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fingerprint", sa.String(length=128), nullable=False, unique=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_giveaway_notification_logs_user_id", "giveaway_notification_logs", ["user_id"])
    op.create_index("ix_giveaway_notification_logs_giveaway_id", "giveaway_notification_logs", ["giveaway_id"])
    op.create_index("ix_giveaway_notification_logs_sent_at", "giveaway_notification_logs", ["sent_at"])


def downgrade() -> None:
    op.drop_table("giveaway_notification_logs")
    op.drop_column("users", "giveaway_notification_kinds")
    op.drop_column("users", "giveaway_notifications_enabled")
