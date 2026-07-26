from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramForbiddenError

from steam_radar.bot.middlewares import CommandCleanupMiddleware


class Message:
    def __init__(self, text: str) -> None:
        self.text = text
        self.deleted = False

    async def delete(self) -> None:
        self.deleted = True


@pytest.mark.asyncio
async def test_admin_command_is_deleted_after_handler() -> None:
    message = Message("/admin")
    called = False

    async def handler(_event, _data):
        nonlocal called
        called = True
        return "ok"

    result = await CommandCleanupMiddleware()(
        handler, SimpleNamespace(), {"event_update": SimpleNamespace(message=message)}
    )
    assert result == "ok"
    assert called
    assert message.deleted


@pytest.mark.asyncio
async def test_regular_command_is_deleted_after_handler() -> None:
    message = Message("/menu")
    handler_saw_existing_message = False

    async def handler(_event, _data):
        nonlocal handler_saw_existing_message
        handler_saw_existing_message = not message.deleted

    await CommandCleanupMiddleware()(handler, SimpleNamespace(), {"event_update": SimpleNamespace(message=message)})
    assert handler_saw_existing_message
    assert message.deleted


@pytest.mark.asyncio
async def test_command_cleanup_ignores_missing_delete_permission() -> None:
    class ForbiddenMessage(Message):
        async def delete(self) -> None:
            raise TelegramForbiddenError(method=SimpleNamespace(), message="forbidden")

    message = ForbiddenMessage("/admin")

    async def handler(_event, _data):
        return "ok"

    assert (
        await CommandCleanupMiddleware()(
            handler,
            SimpleNamespace(),
            {"event_update": SimpleNamespace(message=message)},
        )
        == "ok"
    )
