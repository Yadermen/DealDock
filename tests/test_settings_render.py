from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from aiogram.types import Message

from steam_radar.bot.handlers import _safe_edit_callback, _settings_screen
from steam_radar.db.models import Plan


def fake_user():
    return SimpleNamespace(
        country_code="KZ",
        language_code="ru",
        plan=Plan.FREE,
        premium_until=None,
        is_premium=False,
    )


def test_settings_screen_has_text_and_nonempty_keyboard() -> None:
    content, keyboard = _settings_screen(fake_user())
    assert "Профиль" in content
    assert "Казахстан" in content
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert buttons
    assert any(button.callback_data == "menu:home" for button in buttons)


@pytest.mark.asyncio
async def test_settings_edit_updates_existing_message() -> None:
    message = MagicMock(spec=Message)
    message.edit_text = AsyncMock()
    callback = SimpleNamespace(
        message=message,
        bot=SimpleNamespace(send_message=AsyncMock()),
        from_user=SimpleNamespace(id=1),
    )
    content, keyboard = _settings_screen(fake_user())
    await _safe_edit_callback(callback, content, keyboard)
    message.edit_text.assert_awaited_once_with(content, reply_markup=keyboard)
    callback.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_not_modified_refreshes_markup_without_new_message() -> None:
    message = MagicMock(spec=Message)
    message.edit_text = AsyncMock(
        side_effect=TelegramBadRequest(
            method=EditMessageText(chat_id=1, message_id=1, text="x"),
            message="Bad Request: message is not modified",
        )
    )
    message.edit_reply_markup = AsyncMock()
    callback = SimpleNamespace(
        message=message,
        bot=SimpleNamespace(send_message=AsyncMock()),
        from_user=SimpleNamespace(id=1),
    )
    content, keyboard = _settings_screen(fake_user())
    await _safe_edit_callback(callback, content, keyboard)
    message.edit_reply_markup.assert_awaited_once_with(reply_markup=keyboard)
    callback.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_callback_message_sends_to_user_chat() -> None:
    bot = SimpleNamespace(send_message=AsyncMock())
    callback = SimpleNamespace(message=None, bot=bot, from_user=SimpleNamespace(id=123))
    content, keyboard = _settings_screen(fake_user())
    await _safe_edit_callback(callback, content, keyboard)
    bot.send_message.assert_awaited_once_with(123, content, reply_markup=keyboard)
