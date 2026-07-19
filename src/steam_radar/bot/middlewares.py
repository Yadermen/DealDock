from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import TelegramObject
from sqlalchemy import update

from steam_radar.db.models import User


class ActivityMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        telegram_user = data.get("event_from_user")
        session_factory = data.get("session_factory")
        if telegram_user and session_factory:
            async with session_factory() as session:
                await session.execute(
                    update(User).where(User.telegram_id == telegram_user.id).values(last_seen_at=datetime.now(UTC))
                )
                await session.commit()
        return await handler(event, data)


class CommandCleanupMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update = data.get("event_update") or event
        message = getattr(update, "message", None)
        if message and message.text and message.text.startswith("/"):
            try:
                await message.delete()
            except TelegramBadRequest:
                pass
        return await handler(event, data)
