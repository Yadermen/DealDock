"""Initial Steam Radar schema.

Revision ID: 0001
Revises:
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(64)),
        sa.Column("language_code", sa.String(5)),
        sa.Column("country_code", sa.String(2)),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("plan", sa.Enum("FREE", "PREMIUM", name="plan"), nullable=False),
        sa.Column("premium_until", sa.DateTime(timezone=True)),
        sa.Column("notifications_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("telegram_id", name="uq_users_telegram_id"),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"])
    op.create_table(
        "games",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("steam_app_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("header_image", sa.Text()),
        sa.Column("game_type", sa.String(30), nullable=False),
        sa.Column("is_free", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("steam_app_id", name="uq_games_steam_app_id"),
    )
    op.create_index("ix_games_steam_app_id", "games", ["steam_app_id"])
    op.create_index("ix_games_name", "games", ["name"])
    op.create_table(
        "giveaways",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("store", sa.String(50), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("KEEP", "WEEKEND", "DLC", "FREE_TO_PLAY", name="giveawaykind"),
            nullable=False,
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "watch_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("max_price", sa.Numeric(12, 2)),
        sa.Column("min_discount", sa.Integer()),
        sa.Column("historical_low_only", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_notified_price", sa.Numeric(12, 2)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("uq_watch_user_game", "watch_rules", ["user_id", "game_id"], unique=True)
    op.create_table(
        "price_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("initial_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("final_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("discount_percent", sa.Integer(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_price_snapshots_game_id", "price_snapshots", ["game_id"])
    op.create_index("ix_price_snapshots_checked_at", "price_snapshots", ["checked_at"])
    op.create_index("ix_price_game_country_time", "price_snapshots", ["game_id", "country_code", "checked_at"])
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("telegram_charge_id", sa.String(200), nullable=False, unique=True),
        sa.Column("amount_stars", sa.Integer(), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_payments_user_id", "payments", ["user_id"])
    op.create_table(
        "notification_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("discount_percent", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_notification_logs_user_id", "notification_logs", ["user_id"])
    op.create_index("ix_notification_logs_game_id", "notification_logs", ["game_id"])


def downgrade() -> None:
    op.drop_table("notification_logs")
    op.drop_table("payments")
    op.drop_table("price_snapshots")
    op.drop_table("watch_rules")
    op.drop_table("giveaways")
    op.drop_table("games")
    op.drop_table("users")
    sa.Enum(name="giveawaykind").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="plan").drop(op.get_bind(), checkfirst=True)
