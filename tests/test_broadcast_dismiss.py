from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage

from steam_radar.bot.admin import _deliver_admin_broadcast
from steam_radar.bot.handlers import close_current_message
from steam_radar.bot.keyboards import broadcast_dismiss_keyboard, deal_broadcast_keyboard
from steam_radar.i18n import text


class RecordingBot:
    def __init__(self) -> None:
        self.copies: list[dict] = []
        self.messages: list[dict] = []

    async def copy_message(self, **kwargs):
        self.copies.append(kwargs)

    async def send_message(self, *args, **kwargs):
        self.messages.append({"args": args, **kwargs})


def _user(language: str):
    return SimpleNamespace(telegram_id=123, language_code=language)


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["text", "photo", "video", "document", "animation"])
async def test_every_supported_admin_broadcast_copy_has_close_button(content_type: str) -> None:
    bot = RecordingBot()
    await _deliver_admin_broadcast(
        bot,
        _user("en"),
        {
            "source_chat_id": 10,
            "source_message_id": 20,
            "content_type": content_type,
        },
    )
    assert len(bot.copies) == 1
    button = bot.copies[0]["reply_markup"].inline_keyboard[0][0]
    assert button.callback_data == "ui:close"
    assert button.text == "❌ Close"


@pytest.mark.asyncio
async def test_legacy_text_broadcast_also_has_close_button() -> None:
    bot = RecordingBot()
    await _deliver_admin_broadcast(bot, _user("pl"), {"content": "Hello", "content_type": "text"})
    button = bot.messages[0]["reply_markup"].inline_keyboard[0][0]
    assert button.callback_data == "ui:close"
    assert button.text == "❌ Zamknij"


@pytest.mark.parametrize("language", ["ru", "en", "pl", "uk"])
def test_broadcast_close_is_localized_in_every_broadcast_keyboard(language: str) -> None:
    expected = text(language, "btn_broadcast_close")
    assert broadcast_dismiss_keyboard(language).inline_keyboard[0][0].text == expected
    assert deal_broadcast_keyboard(language).inline_keyboard[-1][0].text == expected


class Callback:
    def __init__(self, fail_delete: bool = False) -> None:
        self.answered = 0
        self.deleted = 0
        self.fail_delete = fail_delete
        self.message = self

    async def answer(self) -> None:
        self.answered += 1

    async def delete(self) -> None:
        self.deleted += 1
        if self.fail_delete:
            raise TelegramBadRequest(
                method=DeleteMessage(chat_id=1, message_id=2),
                message="message to delete not found",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_delete", [False, True])
async def test_close_answers_and_safely_deletes_exact_message(fail_delete: bool) -> None:
    callback = Callback(fail_delete)
    await close_current_message(callback)  # type: ignore[arg-type]
    assert callback.answered == 1
    assert callback.deleted == 1
