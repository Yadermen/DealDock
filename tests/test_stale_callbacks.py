from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from steam_radar.bot.handlers import expired_callback
from steam_radar.i18n import text


@pytest.mark.asyncio
async def test_stale_callback_is_answered_with_localized_alert(monkeypatch) -> None:
    monkeypatch.setattr("steam_radar.bot.handlers._language", AsyncMock(return_value="pl"))
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        answer=AsyncMock(),
    )

    await expired_callback(callback, SimpleNamespace())

    callback.answer.assert_awaited_once_with(text("pl", "callback_expired"), show_alert=True)
