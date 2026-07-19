from pathlib import Path
from types import SimpleNamespace

from steam_radar.bot.keyboards import (
    invoice_keyboard,
    main_keyboard,
    notification_keyboard,
    watch_card_keyboard,
    watch_list_keyboard,
)


def test_watch_card_has_working_callbacks_and_steam_url() -> None:
    keyboard = watch_card_keyboard(7, 620, "ru")
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    callbacks = {button.callback_data for button in buttons if button.callback_data}
    assert callbacks == {
        "watch_edit_discount:7",
        "watch_edit_price:7",
        "watch_notify:7",
        "watch_filters:7",
        "watch_delete:7",
        "menu:games",
        "menu:home",
    }
    assert any(button.url == "https://store.steampowered.com/app/620" for button in buttons)


def test_watch_card_notification_button_reflects_state() -> None:
    keyboard = watch_card_keyboard(7, 620, "ru", notifications_enabled=False)
    button = next(
        button for row in keyboard.inline_keyboard for button in row if button.callback_data == "watch_notify:7"
    )
    assert "выключены" in button.text


def test_main_callbacks_are_unique() -> None:
    keyboard = main_keyboard("ru")
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert len(callbacks) == len(set(callbacks))
    assert "menu:settings" in callbacks


def test_empty_watch_list_still_has_navigation() -> None:
    keyboard = watch_list_keyboard([], "ru")
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert callbacks == ["menu:search", "menu:home"]


def test_watch_list_opens_each_rule() -> None:
    rule = SimpleNamespace(id=9, game=SimpleNamespace(name="Portal 2"))
    keyboard = watch_list_keyboard([rule], "ru")
    assert keyboard.inline_keyboard[0][0].callback_data == "watch:9"


def test_invoice_has_pay_and_menu_buttons() -> None:
    keyboard = invoice_keyboard("en", 100)
    assert keyboard.inline_keyboard[0][0].pay
    assert keyboard.inline_keyboard[1][0].callback_data == "menu:home"


def test_notification_has_steam_and_close_buttons() -> None:
    keyboard = notification_keyboard("en", 252490)
    assert keyboard.inline_keyboard[0][0].url.endswith("/252490")
    assert keyboard.inline_keyboard[1][0].callback_data == "ui:close"


def test_notification_is_the_only_user_keyboard_with_close_callback() -> None:
    source = (Path(__file__).parents[1] / "src" / "steam_radar" / "bot" / "keyboards.py").read_text(encoding="utf-8")
    assert source.count('callback_data="ui:close"') == 2
