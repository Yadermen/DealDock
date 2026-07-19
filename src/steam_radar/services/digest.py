from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from steam_radar.db.models import NotificationLog, User
from steam_radar.i18n import text


class DigestService:
    def __init__(self, bot: Bot, session_factory: async_sessionmaker) -> None:
        self.bot, self.session_factory = bot, session_factory

    async def run(self) -> int:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            users = list(
                (
                    await session.scalars(select(User).where(User.daily_digest_enabled | User.weekly_digest_enabled))
                ).all()
            )
            sent = 0
            for user in users:
                for kind in ("daily", "weekly"):
                    if not self._due(user, now, kind):
                        continue
                    period = timedelta(days=1 if kind == "daily" else 7)
                    count = await session.scalar(
                        select(func.count())
                        .select_from(NotificationLog)
                        .where(NotificationLog.user_id == user.id, NotificationLog.sent_at >= now - period)
                    )
                    if count:
                        label = text(user.language_code, f"digest_{kind}")
                        await self.bot.send_message(
                            user.telegram_id,
                            f"📊 <b>{label}</b>\n\n" + text(user.language_code, "digest_events", count=count),
                        )
                        sent += 1
                    setattr(user, f"{kind}_digest_last_at", now)
            await session.commit()
        return sent

    @staticmethod
    def _due(user: User, now: datetime, kind: str = "daily") -> bool:
        try:
            local = now.astimezone(ZoneInfo(user.timezone))
        except ZoneInfoNotFoundError:
            local = now
        enabled = getattr(user, f"{kind}_digest_enabled", False)
        digest_time = getattr(user, f"{kind}_digest_time", None)
        if not enabled or digest_time is None or local.hour != digest_time.hour or local.minute >= 15:
            return False
        if kind == "weekly" and local.weekday() != user.weekly_digest_weekday:
            return False
        period = timedelta(hours=23 if kind == "daily" else 24 * 6)
        last_at = getattr(user, f"{kind}_digest_last_at", None)
        return last_at is None or last_at < now - period
