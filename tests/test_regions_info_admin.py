from pathlib import Path
from zoneinfo import ZoneInfo

from steam_radar.bot.admin import (
    _admin_time_boundaries,
    admin_back_keyboard,
    admin_close_keyboard,
    admin_keyboard,
    admin_premium_keyboard,
)
from steam_radar.bot.keyboards import info_keyboard, info_page_keyboard, main_keyboard, timezone_keyboard
from steam_radar.constants import REGIONS
from steam_radar.i18n import TEXTS

EXPECTED_CURRENCIES = {
    "RU": "RUB",
    "BY": "USD",
    "KZ": "KZT",
    "UA": "UAH",
    "AM": "USD",
    "AZ": "USD",
    "GE": "USD",
    "KG": "USD",
    "MD": "USD",
    "TJ": "USD",
    "TM": "USD",
    "UZ": "USD",
    "PL": "PLN",
    "DE": "EUR",
    "FR": "EUR",
    "IT": "EUR",
    "ES": "EUR",
    "CZ": "EUR",
    "SK": "EUR",
    "AT": "EUR",
    "BE": "EUR",
    "NL": "EUR",
    "SE": "EUR",
    "NO": "NOK",
    "FI": "EUR",
    "DK": "EUR",
    "HU": "EUR",
    "RO": "EUR",
    "BG": "EUR",
    "LT": "EUR",
    "LV": "EUR",
    "EE": "EUR",
}


def _callbacks(markup) -> list[str | None]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_all_regions_have_verified_currency_timezone_and_translations() -> None:
    assert {code: REGIONS[code].currency for code in EXPECTED_CURRENCIES} == EXPECTED_CURRENCIES
    for code, region in REGIONS.items():
        assert region.id == code == region.steam_country_code
        ZoneInfo(region.timezone)
        for catalog in TEXTS.values():
            assert catalog[region.translation_key].strip()


def test_information_navigation_exists_in_every_language() -> None:
    for language in TEXTS:
        assert "menu:info" in _callbacks(main_keyboard(language))
        assert {
            "info:search",
            "info:discounts",
            "info:giveaways",
            "info:analytics",
            "info:premium",
            "info:referrals",
            "info:region",
            "info:start",
        }.issubset(set(_callbacks(info_keyboard(language))))
        assert _callbacks(info_page_keyboard(language)) == ["menu:info"]
        assert "info:plans" in _callbacks(info_page_keyboard(language, "premium"))
        assert "menu:info" in _callbacks(info_page_keyboard(language, "premium"))
        assert "ui:close" not in _callbacks(info_keyboard(language))
        assert any(value.startswith("timezone:") for value in _callbacks(timezone_keyboard(language)) if value)


def test_every_admin_keyboard_variant_has_close() -> None:
    for language in TEXTS:
        for markup in (admin_keyboard(language), admin_back_keyboard(language), admin_close_keyboard(language)):
            assert "admin:close" in _callbacks(markup)
            assert "menu:home" not in _callbacks(markup)


def test_admin_user_card_requires_confirmed_delete() -> None:
    callbacks = _callbacks(admin_premium_keyboard("en", 42, False))
    assert "admin_user_delete:42" in callbacks
    assert "admin_user_delete_confirm:42" not in callbacks


def test_admin_panel_uses_matching_database_datetime_boundaries() -> None:
    aware, naive = _admin_time_boundaries()
    assert aware.tzinfo is not None
    assert naive.tzinfo is None


def test_removed_support_commands_are_absent_from_runtime_ui() -> None:
    root = Path(__file__).parents[1] / "src" / "steam_radar"
    runtime = (root / "main.py").read_text(encoding="utf-8") + (root / "bot" / "handlers.py").read_text(
        encoding="utf-8"
    )
    assert 'Command("support"' not in runtime
    assert "paysupport" not in runtime
    for catalog in TEXTS.values():
        assert "support" not in catalog
        assert "/support" not in catalog["help"]
        assert "/admin" not in catalog["help"]
