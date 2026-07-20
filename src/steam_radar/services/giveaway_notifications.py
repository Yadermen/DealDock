import hashlib
from datetime import UTC, datetime
from html import escape

import structlog
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from steam_radar.db.models import Giveaway, GiveawayNotificationLog, User
from steam_radar.i18n import text

log = structlog.get_logger()


class GiveawayNotificationService:
    def __init__(self, bot: Bot, session_factory: async_sessionmaker) -> None:
        self.bot = bot
        self.session_factory = session_factory

    async def run(self) -> int:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            users = list(
                (await session.scalars(select(User).where(User.giveaway_notifications_enabled.is_(True)))).all()
            )
            giveaways = list(
                (
                    await session.scalars(
                        select(Giveaway).where(
                            Giveaway.approved.is_(True),
                            Giveaway.active.is_(True),
                            (Giveaway.ends_at.is_(None) | (Giveaway.ends_at > now)),
                        )
                    )
                ).all()
            )
        sent = 0
        for user in users:
            for item in giveaways:
                if not self.accepts(user, item):
                    continue
                fingerprint = self.fingerprint(user.id, item)
                async with self.session_factory() as session:
                    exists = await session.scalar(
                        select(GiveawayNotificationLog.id).where(GiveawayNotificationLog.fingerprint == fingerprint)
                    )
                if exists:
                    continue
                try:
                    await self.bot.send_message(
                        user.telegram_id,
                        self.build_message(item, user.language_code or "ru"),
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                        reply_markup=InlineKeyboardMarkup(
                            inline_keyboard=[
                                [InlineKeyboardButton(text=text(user.language_code, "btn_open_offer"), url=item.url)],
                                [
                                    InlineKeyboardButton(
                                        text=text(user.language_code, "btn_close"), callback_data="ui:close"
                                    )
                                ],
                            ]
                        ),
                    )
                    async with self.session_factory() as session:
                        await session.execute(
                            pg_insert(GiveawayNotificationLog)
                            .values(
                                user_id=user.id,
                                giveaway_id=item.id,
                                fingerprint=fingerprint,
                                sent_at=now,
                            )
                            .on_conflict_do_nothing(index_elements=[GiveawayNotificationLog.fingerprint])
                        )
                        await session.commit()
                    sent += 1
                except Exception as error:
                    log.warning(
                        "giveaway_notification_failed",
                        user_id=user.id,
                        giveaway_id=item.id,
                        exception_type=type(error).__name__,
                    )
        return sent

    @staticmethod
    def accepts(user: User, item: Giveaway) -> bool:
        if not user.giveaway_notifications_enabled:
            return False
        if not user.is_premium:
            return True
        return item.kind.value in set(user.giveaway_notification_kinds or [])

    @staticmethod
    def fingerprint(user_id: int, item: Giveaway) -> str:
        raw = "|".join(
            [
                str(user_id),
                str(item.external_id or item.id),
                item.store,
                item.kind.value,
                item.starts_at.isoformat() if item.starts_at else "",
                item.ends_at.isoformat() if item.ends_at else "",
            ]
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def build_message(item: Giveaway, language: str) -> str:
        kind = text(language, f"giveaway_{item.kind.value}")
        permanence = text(
            language,
            "giveaway_library_keep" if item.kind.value in {"keep", "dlc"} else "giveaway_library_temporary",
        )
        ends = (
            text(language, "giveaway_ends", value=item.ends_at.strftime("%d.%m.%Y, %H:%M UTC")) if item.ends_at else ""
        )
        return text(
            language,
            "giveaway_notification_card",
            title=escape(item.title),
            kind=kind,
            store=escape(item.store),
            permanence=permanence,
            ends=ends,
        )
