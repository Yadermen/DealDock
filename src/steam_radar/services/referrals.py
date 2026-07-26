import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from steam_radar.constants import (
    REFERRAL_INVITEE_DAYS,
    REFERRAL_INVITER_DAYS,
    REFERRAL_LEVELS,
    ReferralLevel,
)
from steam_radar.db.models import (
    Plan,
    Referral,
    ReferralMilestoneAward,
    ReferralReward,
    User,
    WatchRule,
)
from steam_radar.i18n import text

log = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class ReferralDashboard:
    code: str
    invited: int
    active: int
    earned_days: int
    next_level: ReferralLevel | None
    awarded_levels: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class LeaderboardEntry:
    position: int
    user_id: int
    display_name: str
    active_referrals: int
    badge: str | None


class ReferralService:
    CAMPAIGN = "permanent"

    def __init__(self, bot: Bot, session_factory: async_sessionmaker) -> None:
        self.bot = bot
        self.session_factory = session_factory
        self._bot_username: str | None = None

    async def register_pending(self, session: AsyncSession, user: User, code: str | None) -> bool:
        """Bind an inviter once, and only for a user created by the current /start."""
        if not code or user.referred_by_user_id is not None:
            return False
        inviter = await session.scalar(select(User).where(User.referral_code == code))
        if inviter is None or inviter.id == user.id:
            return False
        user.referred_by_user_id = inviter.id
        session.add(
            Referral(
                inviter_user_id=inviter.id,
                referred_user_id=user.id,
                referral_code=code,
                campaign_key=self.CAMPAIGN,
                status="pending",
                registered_at=datetime.now(UTC),
            )
        )
        return True

    async def mark_onboarding_completed(self, session: AsyncSession, user: User) -> None:
        now = datetime.now(UTC)
        user.onboarding_completed_at = user.onboarding_completed_at or now
        referral = await session.scalar(select(Referral).where(Referral.referred_user_id == user.id))
        if referral and referral.onboarding_completed_at is None:
            referral.onboarding_completed_at = now

    async def activate_if_eligible(self, referred_user_id: int) -> bool:
        milestone_levels: list[ReferralLevel] = []
        async with self.session_factory() as session:
            referral = await session.scalar(
                select(Referral)
                .where(Referral.referred_user_id == referred_user_id)
                .with_for_update()
            )
            if referral is None or referral.status == "active":
                return False
            referred = await session.get(User, referred_user_id)
            if referred is None or referred.onboarding_completed_at is None:
                return False
            has_watch = await session.scalar(
                select(WatchRule.id).where(
                    WatchRule.user_id == referred.id,
                    WatchRule.enabled.is_(True),
                )
            )
            if has_watch is None:
                return False
            inviter = await session.scalar(
                select(User).where(User.id == referral.inviter_user_id).with_for_update()
            )
            if inviter is None:
                return False

            now = datetime.now(UTC)
            referral.status = "active"
            referral.activated_at = now
            self._grant_days(referred, REFERRAL_INVITEE_DAYS, now)
            self._grant_days(inviter, REFERRAL_INVITER_DAYS, now)
            session.add_all(
                [
                    self._reward(referred.id, referral.id, "invitee_activation", REFERRAL_INVITEE_DAYS, now),
                    self._reward(inviter.id, referral.id, "inviter_activation", REFERRAL_INVITER_DAYS, now),
                ]
            )

            active_count = int(
                await session.scalar(
                    select(func.count(Referral.id)).where(
                        Referral.inviter_user_id == inviter.id,
                        Referral.status == "active",
                    )
                )
                or 0
            )
            awarded = set(
                (
                    await session.scalars(
                        select(ReferralMilestoneAward.level_key).where(
                            ReferralMilestoneAward.user_id == inviter.id,
                            ReferralMilestoneAward.campaign_key == self.CAMPAIGN,
                        )
                    )
                ).all()
            )
            for level in eligible_referral_levels(active_count, awarded):
                self._grant_days(inviter, level.reward_days, now)
                if level.badge:
                    inviter.referral_badge = level.badge
                session.add(
                    ReferralMilestoneAward(
                        user_id=inviter.id,
                        campaign_key=self.CAMPAIGN,
                        level_key=level.key,
                        premium_days=level.reward_days,
                        awarded_at=now,
                    )
                )
                session.add(self._reward(inviter.id, referral.id, f"level_{level.key}", level.reward_days, now))
                milestone_levels.append(level)
            await session.commit()
            inviter_notice = (inviter.telegram_id, inviter.language_code or "ru", inviter.premium_until)
            referred_notice = (referred.telegram_id, referred.language_code or "ru", referred.premium_until)

        await self._notify_activation(inviter_notice, referred_notice, milestone_levels)
        return True

    async def dashboard(self, telegram_id: int) -> ReferralDashboard | None:
        async with self.session_factory() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
            if user is None:
                return None
            code = await self._ensure_code(session, user)
            invited = int(
                await session.scalar(
                    select(func.count(Referral.id)).where(Referral.inviter_user_id == user.id)
                )
                or 0
            )
            active = int(
                await session.scalar(
                    select(func.count(Referral.id)).where(
                        Referral.inviter_user_id == user.id,
                        Referral.status == "active",
                    )
                )
                or 0
            )
            await session.commit()
            next_level = next(
                (level for level in REFERRAL_LEVELS if level.active_referrals > active),
                None,
            )
            awarded = frozenset(
                (
                    await session.scalars(
                        select(ReferralMilestoneAward.level_key).where(
                            ReferralMilestoneAward.user_id == user.id,
                            ReferralMilestoneAward.campaign_key == self.CAMPAIGN,
                        )
                    )
                ).all()
            )
            return ReferralDashboard(code, invited, active, user.referral_days_earned, next_level, awarded)

    async def referral_link(self, code: str) -> str:
        if self._bot_username is None:
            self._bot_username = (await self.bot.get_me()).username
        return f"https://t.me/{self._bot_username}?start={code}"

    async def inline_invitation(self, telegram_id: int, code: str) -> tuple[str, str] | None:
        """Return language/link only when the inline-query code belongs to its sender."""
        async with self.session_factory() as session:
            user = await session.scalar(
                select(User).where(
                    User.telegram_id == telegram_id,
                    User.referral_code == code,
                )
            )
        if user is None:
            return None
        return user.language_code or "ru", await self.referral_link(code)

    async def leaderboard(
        self,
        telegram_id: int,
        limit: int = 100,
    ) -> tuple[list[LeaderboardEntry], LeaderboardEntry | None]:
        counts = (
            select(
                Referral.inviter_user_id.label("user_id"),
                func.count(Referral.id).label("active_count"),
            )
            .where(Referral.status == "active")
            .group_by(Referral.inviter_user_id)
            .subquery()
        )
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(User, counts.c.active_count)
                    .join(counts, counts.c.user_id == User.id)
                    .order_by(counts.c.active_count.desc(), User.id)
                    .limit(limit)
                )
            ).all()
            entries = [
                LeaderboardEntry(
                    position=index,
                    user_id=user.id,
                    display_name=user.username or user.display_name or f"ID {user.telegram_id}",
                    active_referrals=int(active),
                    badge=user.referral_badge,
                )
                for index, (user, active) in enumerate(rows, 1)
            ]
            current = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            outside: LeaderboardEntry | None = None
            if current and all(entry.user_id != current.id for entry in entries):
                own_count = int(
                    await session.scalar(
                        select(func.count(Referral.id)).where(
                            Referral.inviter_user_id == current.id,
                            Referral.status == "active",
                        )
                    )
                    or 0
                )
                higher = int(
                    await session.scalar(
                        select(func.count()).select_from(counts).where(counts.c.active_count > own_count)
                    )
                    or 0
                )
                outside = LeaderboardEntry(
                    higher + 1,
                    current.id,
                    current.username or current.display_name or f"ID {current.telegram_id}",
                    own_count,
                    current.referral_badge,
                )
        return entries, outside

    async def _ensure_code(self, session: AsyncSession, user: User) -> str:
        if user.referral_code:
            return user.referral_code
        while True:
            code = secrets.token_urlsafe(8)
            if await session.scalar(select(User.id).where(User.referral_code == code)) is None:
                user.referral_code = code
                return code

    @staticmethod
    def _grant_days(user: User, days: int, now: datetime) -> None:
        active = user.plan == Plan.PREMIUM and user.premium_until is not None and user.premium_until > now
        user.premium_until = (user.premium_until if active else now) + timedelta(days=days)
        user.plan = Plan.PREMIUM
        user.premium_started_at = user.premium_started_at or now
        user.premium_source = "referral"
        user.premium_expired_notified_at = None
        user.referral_days_earned += days

    def _reward(self, user_id: int, referral_id: int, key: str, days: int, now: datetime) -> ReferralReward:
        return ReferralReward(
            user_id=user_id,
            referral_id=referral_id,
            reward_key=key,
            campaign_key=self.CAMPAIGN,
            premium_days=days,
            created_at=now,
        )

    async def _notify_activation(
        self,
        inviter: tuple[int, str, datetime | None],
        referred: tuple[int, str, datetime | None],
        levels: list[ReferralLevel],
    ) -> None:
        def markup(language: str) -> InlineKeyboardMarkup:
            return InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=text(language, "btn_close"),
                            callback_data="ui:close",
                        )
                    ]
                ]
            )

        try:
            await self.bot.send_message(
                referred[0],
                text(
                    referred[1],
                    "referral_invitee_activated",
                    days=REFERRAL_INVITEE_DAYS,
                    premium_until=referred[2].strftime("%d.%m.%Y") if referred[2] else "—",
                ),
                reply_markup=markup(referred[1]),
            )
        except Exception:
            log.exception("referral_invitee_notification_failed", telegram_id=referred[0])
        try:
            total_days = REFERRAL_INVITER_DAYS + sum(level.reward_days for level in levels)
            achievements = ", ".join(text(inviter[1], f"referral_level_{level.key}") for level in levels)
            await self.bot.send_message(
                inviter[0],
                text(
                    inviter[1],
                    "referral_inviter_activated",
                    days=total_days,
                    achievement=(
                        text(inviter[1], "referral_achievement_line", achievement=achievements)
                        if achievements
                        else ""
                    ),
                    premium_until=inviter[2].strftime("%d.%m.%Y") if inviter[2] else "—",
                ),
                reply_markup=markup(inviter[1]),
            )
        except Exception:
            log.exception("referral_inviter_notification_failed", telegram_id=inviter[0])


def eligible_referral_levels(active_count: int, awarded: set[str]) -> list[ReferralLevel]:
    return [
        level
        for level in REFERRAL_LEVELS
        if active_count >= level.active_referrals and level.key not in awarded
    ]
