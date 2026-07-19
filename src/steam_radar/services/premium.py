from datetime import UTC, datetime

import structlog
from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from steam_radar.db.models import Plan, User
from steam_radar.i18n import text

log = structlog.get_logger()


class PremiumService:
    def __init__(self, bot: Bot, session_factory: async_sessionmaker) -> None:
        self.bot, self.session_factory = bot, session_factory

    async def expire_subscriptions(self) -> int:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            users = list(
                (await session.scalars(select(User).where(User.plan == Plan.PREMIUM, User.premium_until <= now))).all()
            )
            expired = 0
            for user in users:
                user.plan = Plan.FREE
                if user.premium_expired_notified_at is None:
                    try:
                        await self.bot.send_message(user.telegram_id, text(user.language_code, "premium_expired"))
                    except Exception:
                        log.exception("premium_expiration_notification_failed", user_id=user.id)
                    user.premium_expired_notified_at = now
                expired += 1
            await session.commit()
        return expired
