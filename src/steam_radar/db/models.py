from datetime import datetime, time
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from steam_radar.db.base import Base, TimestampMixin


class Plan(StrEnum):
    FREE = "free"
    PREMIUM = "premium"


class GiveawayKind(StrEnum):
    KEEP = "keep"
    WEEKEND = "weekend"
    DLC = "dlc"
    FREE_TO_PLAY = "free_to_play"


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_telegram_id", "telegram_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str | None] = mapped_column(String(128))
    language_code: Mapped[str | None] = mapped_column(String(5))
    country_code: Mapped[str | None] = mapped_column(String(2))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    plan: Mapped[Plan] = mapped_column(Enum(Plan), default=Plan.FREE)
    premium_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    premium_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    premium_expired_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    premium_source: Mapped[str | None] = mapped_column(String(32))
    comparison_currency: Mapped[str] = mapped_column(String(3), default="USD")
    comparison_regions: Mapped[list[str]] = mapped_column(JSON, default=list)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    giveaway_notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    giveaway_notification_kinds: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["keep", "weekend", "dlc"])
    quiet_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    quiet_hours_start: Mapped[time | None] = mapped_column(Time())
    quiet_hours_end: Mapped[time | None] = mapped_column(Time())
    digest_mode: Mapped[str] = mapped_column(String(10), default="off")
    digest_time: Mapped[time | None] = mapped_column(Time())
    digest_weekday: Mapped[int] = mapped_column(Integer, default=0)
    last_digest_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    daily_digest_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    daily_digest_time: Mapped[time | None] = mapped_column(Time())
    daily_digest_last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    weekly_digest_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    weekly_digest_time: Mapped[time | None] = mapped_column(Time())
    weekly_digest_weekday: Mapped[int] = mapped_column(Integer, default=0)
    weekly_digest_last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    referral_code: Mapped[str | None] = mapped_column(String(24), unique=True, index=True)
    referred_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    referral_badge: Mapped[str | None] = mapped_column(String(32))
    referral_days_earned: Mapped[int] = mapped_column(Integer, default=0)
    watches: Mapped[list["WatchRule"]] = relationship(back_populates="user", cascade="all, delete")

    @property
    def is_premium(self) -> bool:
        if self.plan != Plan.PREMIUM or self.premium_until is None:
            return False
        return self.premium_until.timestamp() > datetime.now().astimezone().timestamp()


class Game(TimestampMixin, Base):
    __tablename__ = "games"
    __table_args__ = (
        Index("ix_games_steam_app_id", "steam_app_id"),
        Index(
            "ix_games_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    steam_app_id: Mapped[int] = mapped_column(unique=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    header_image: Mapped[str | None] = mapped_column(Text)
    game_type: Mapped[str] = mapped_column(String(30), default="game")
    is_free: Mapped[bool] = mapped_column(Boolean, default=False)
    watches: Mapped[list["WatchRule"]] = relationship(back_populates="game")


class SteamCatalogApp(TimestampMixin, Base):
    """Search-only Steam catalogue; never represents a user's tracked game."""

    __tablename__ = "steam_catalog_apps"
    __table_args__ = (
        Index(
            "ix_steam_catalog_apps_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        Index(
            "ix_steam_catalog_apps_search_text_trgm",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
    )
    steam_app_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    search_text: Mapped[str] = mapped_column(Text)


class Referral(Base):
    __tablename__ = "referrals"
    id: Mapped[int] = mapped_column(primary_key=True)
    inviter_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    referred_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    referral_code: Mapped[str] = mapped_column(String(24))
    campaign_key: Mapped[str] = mapped_column(String(50), default="permanent", index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReferralReward(Base):
    __tablename__ = "referral_rewards"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "referral_id",
            "campaign_key",
            "reward_key",
            name="uq_referral_reward_once",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    referral_id: Mapped[int | None] = mapped_column(ForeignKey("referrals.id", ondelete="CASCADE"), index=True)
    reward_key: Mapped[str] = mapped_column(String(50))
    campaign_key: Mapped[str] = mapped_column(String(50), default="permanent", index=True)
    premium_days: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ReferralMilestoneAward(Base):
    __tablename__ = "referral_milestone_awards"
    __table_args__ = (
        UniqueConstraint("user_id", "campaign_key", "level_key", name="uq_referral_milestone_once"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    campaign_key: Mapped[str] = mapped_column(String(50), default="permanent")
    level_key: Mapped[str] = mapped_column(String(50))
    premium_days: Mapped[int] = mapped_column(Integer)
    awarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WatchRule(TimestampMixin, Base):
    __tablename__ = "watch_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    max_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    target_currency: Mapped[str | None] = mapped_column(String(3))
    min_discount: Mapped[int | None]
    historical_low_only: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_on_new_historical_low: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_on_known_historical_low: Mapped[bool] = mapped_column(Boolean, default=False)
    minimum_price_drop_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    minimum_price_drop_percent: Mapped[int | None]
    notify_on_any_price_drop: Mapped[bool] = mapped_column(Boolean, default=False)
    repeat_notification_policy: Mapped[str] = mapped_column(String(20), default="on_change")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_notified_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    last_notified_discount: Mapped[int | None]
    last_notification_type: Mapped[str | None] = mapped_column(String(30))
    last_notification_fingerprint: Mapped[str | None] = mapped_column(String(128))
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    condition_was_met: Mapped[bool] = mapped_column(Boolean, default=False)
    user: Mapped[User] = relationship(back_populates="watches")
    game: Mapped[Game] = relationship(back_populates="watches")
    __table_args__ = (Index("uq_watch_user_game", "user_id", "game_id", unique=True),)


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    country_code: Mapped[str] = mapped_column(String(2))
    currency: Mapped[str] = mapped_column(String(3))
    initial_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    final_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    discount_percent: Mapped[int]
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (Index("ix_price_game_country_time", "game_id", "country_code", "checked_at"),)


class ExternalHistoricalLow(Base):
    __tablename__ = "external_historical_lows"
    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    country_code: Mapped[str] = mapped_column(String(2))
    scope: Mapped[str] = mapped_column(String(20))
    shop_name: Mapped[str | None] = mapped_column(String(100))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (Index("uq_external_low_game_country_scope", "game_id", "country_code", "scope", unique=True),)


class CurrencyRateCache(Base):
    __tablename__ = "currency_rate_cache"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    base_currency: Mapped[str] = mapped_column(String(3), default="USD")
    rates: Mapped[dict] = mapped_column(JSON)
    provider_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PriceHistoryCache(Base):
    __tablename__ = "price_history_cache"
    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    country_code: Mapped[str] = mapped_column(String(2))
    currency: Mapped[str] = mapped_column(String(3))
    source: Mapped[str] = mapped_column(String(32), default="isthereanydeal")
    payload: Mapped[list[dict]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (Index("uq_price_history_game_country", "game_id", "country_code", unique=True),)


class Giveaway(TimestampMixin, Base):
    __tablename__ = "giveaways"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(Text)
    store: Mapped[str] = mapped_column(String(50), default="Steam")
    kind: Mapped[GiveawayKind] = mapped_column(Enum(GiveawayKind))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(50), default="manual")
    external_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    image_url: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GiveawayNotificationLog(Base):
    __tablename__ = "giveaway_notification_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    giveaway_id: Mapped[int] = mapped_column(ForeignKey("giveaways.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(128), unique=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed: Mapped[int] = mapped_column(default=0)
    error_count: Mapped[int] = mapped_column(default=0)


class SystemError(Base):
    __tablename__ = "system_errors"
    id: Mapped[int] = mapped_column(primary_key=True)
    component: Mapped[str] = mapped_column(String(80), index=True)
    message: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    telegram_charge_id: Mapped[str] = mapped_column(String(200), unique=True)
    amount_stars: Mapped[int]
    months: Mapped[int]
    duration_days: Mapped[int] = mapped_column(Integer, default=0)
    tariff: Mapped[str] = mapped_column(String(32), default="premium")
    status: Mapped[str] = mapped_column(String(20), default="successful", index=True)
    source: Mapped[str] = mapped_column(String(32), default="telegram_stars")
    username: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str | None] = mapped_column(String(128))
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)


class DeferredNotification(Base):
    __tablename__ = "deferred_notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    game_id: Mapped[int | None] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    steam_app_id: Mapped[int | None]
    keyboard_type: Mapped[str] = mapped_column(String(20), default="notification")
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    notification_type: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    send_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationLog(Base):
    __tablename__ = "notification_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    discount_percent: Mapped[int]
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PremiumAudit(Base):
    __tablename__ = "premium_audits"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    admin_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(32))
    old_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AdminUserAudit(Base):
    __tablename__ = "admin_user_audits"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    admin_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    field: Mapped[str] = mapped_column(String(32))
    old_value: Mapped[str | None] = mapped_column(String(500))
    new_value: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DealBroadcastRun(Base):
    __tablename__ = "deal_broadcast_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    admin_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    respect_quiet_hours: Mapped[bool] = mapped_column(Boolean, default=True)
    users_checked: Mapped[int] = mapped_column(default=0)
    messages_sent: Mapped[int] = mapped_column(default=0)
    no_deals: Mapped[int] = mapped_column(default=0)
    deferred: Mapped[int] = mapped_column(default=0)
    blocked: Mapped[int] = mapped_column(default=0)
    temporary_errors: Mapped[int] = mapped_column(default=0)
    permanent_errors: Mapped[int] = mapped_column(default=0)
    games_included: Mapped[int] = mapped_column(default=0)
    stale_games: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (
        Index(
            "uq_active_deal_broadcast",
            sql_text("(1)"),
            unique=True,
            postgresql_where=sql_text("status IN ('pending', 'running')"),
        ),
    )


class DealBroadcastRecipient(Base):
    __tablename__ = "deal_broadcast_recipients"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("deal_broadcast_runs.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    message_count: Mapped[int] = mapped_column(default=0)
    game_count: Mapped[int] = mapped_column(default=0)
    content_pages: Mapped[list[str] | None] = mapped_column(JSON)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("run_id", "user_id", name="uq_deal_broadcast_run_user"),)
