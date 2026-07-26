from datetime import datetime, timedelta

import pytest

from steam_radar.bot.keyboards import (
    manual_timezone_keyboard,
    timezone_groups_keyboard,
    timezone_keyboard,
)
from steam_radar.i18n import TEXTS, text
from steam_radar.services.timezones import normalize_utc_offset, timezone_from_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+02:00", "UTC+02:00"),
        ("UTC-05:30", "UTC-05:30"),
        (" -00:00 ", "UTC"),
        ("+14:00", "UTC+14:00"),
    ],
)
def test_manual_utc_offset_validation(raw: str, expected: str) -> None:
    assert normalize_utc_offset(raw) == expected


@pytest.mark.parametrize("raw", ["2:00", "+15:00", "+14:30", "+02:60", "Europe/Warsaw", ""])
def test_invalid_manual_utc_offset_is_rejected(raw: str) -> None:
    assert normalize_utc_offset(raw) is None


def test_fixed_offset_and_iana_timezone_share_one_resolver() -> None:
    assert timezone_from_name("UTC+05:30").utcoffset(None) == timedelta(hours=5, minutes=30)
    assert timezone_from_name("Europe/Warsaw").utcoffset(datetime(2026, 7, 25)) is not None


@pytest.mark.parametrize("language", ["ru", "en", "pl", "uk"])
def test_mandatory_timezone_selection_has_no_cancel_or_menu(language: str) -> None:
    keyboards = (
        timezone_groups_keyboard(language, onboarding=True),
        timezone_keyboard(language, "europe", onboarding=True),
        manual_timezone_keyboard(language, onboarding=True),
    )
    callbacks = {
        button.callback_data
        for keyboard in keyboards
        for row in keyboard.inline_keyboard
        for button in row
    }
    assert "timezone:manual" in callbacks
    assert "timezone_groups" in callbacks
    assert "onboarding:timezone" in callbacks
    assert "onboarding:cancel" not in callbacks
    assert "menu:home" not in callbacks
    assert any(
        button.text == text(language, "btn_back_timezone_list")
        for keyboard in keyboards[1:]
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_timezone_settings_keep_navigation_controls() -> None:
    groups = timezone_groups_keyboard("en")
    zone = timezone_keyboard("en", "europe")
    manual = manual_timezone_keyboard("en")
    callbacks = {
        button.callback_data
        for keyboard in (groups, zone, manual)
        for row in keyboard.inline_keyboard
        for button in row
    }
    assert "menu:home" in callbacks
    assert "settings:timezone" in callbacks


def test_first_welcome_contains_only_russian_and_english() -> None:
    welcome = text("ru", "welcome_multilingual")
    for phrase in ("Добро пожаловать", "Welcome"):
        assert phrase in welcome
    for phrase in ("Ласкаво просимо", "Witamy"):
        assert phrase not in welcome


def test_user_localizations_use_dealdock_brand() -> None:
    for catalog in TEXTS.values():
        assert all("Steam Radar" not in value for value in catalog.values())
