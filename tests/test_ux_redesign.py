from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from steam_radar.bot.admin import _admin_users_markup, admin_keyboard, admin_premium_root_keyboard
from steam_radar.bot.handlers import _deal_value_score, _parse_deal_discount, _parse_deal_price
from steam_radar.bot.keyboards import (
    deals_sort_keyboard,
    digest_keyboard,
    digest_kind_keyboard,
    giveaways_keyboard,
    premium_gate_keyboard,
    profile_keyboard,
    watch_card_keyboard,
)
from steam_radar.i18n import TEXTS, text


def callbacks(keyboard) -> list[str]:
    return [button.callback_data for row in keyboard.inline_keyboard for button in row if button.callback_data]


@pytest.mark.parametrize("language", ["ru", "en", "uk", "pl"])
def test_watch_card_has_real_analytics_labels(language: str) -> None:
    keyboard = watch_card_keyboard(7, 252490, language)
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert text(language, "btn_analytics") in labels
    assert text(language, "btn_compare") in labels
    assert text(language, "ui_error") not in labels


def test_free_giveaway_and_profile_keep_premium_entries_visible() -> None:
    free = SimpleNamespace(is_premium=False, giveaway_notifications_enabled=True)
    assert "giveaway:settings" in callbacks(giveaways_keyboard(free, "en"))
    profile_callbacks = callbacks(profile_keyboard("en"))
    assert "settings:quiet" in profile_callbacks
    assert "settings:digest" in profile_callbacks


def test_paywall_has_purchase_and_return_actions() -> None:
    keyboard = premium_gate_keyboard("en", "menu:games", 12, 900)
    values = callbacks(keyboard)
    assert "buy:12:900" in values
    assert "menu:games" in values


def test_deal_rating_is_bounded_and_requires_enough_data() -> None:
    now = datetime.now(UTC)
    item = SimpleNamespace(
        final_price=Decimal("60"),
        initial_price=Decimal("100"),
        discount_percent=40,
        currency="PLN",
        checked_at=now,
    )
    assert _deal_value_score(item, None, [], now) is None
    low = SimpleNamespace(price=Decimal("50"), currency="PLN")
    score = _deal_value_score(item, low, [], now)
    assert score is not None and 0 <= score <= 100


@pytest.mark.parametrize(("raw", "expected"), [("25", Decimal("25")), ("50,5", Decimal("50.5"))])
def test_manual_discount_filter(raw: str, expected: Decimal) -> None:
    assert _parse_deal_discount(raw) == expected


@pytest.mark.parametrize("raw", ["-1", "100.01", "NaN"])
def test_manual_discount_filter_rejects_invalid_values(raw: str) -> None:
    with pytest.raises((ValueError, ArithmeticError)):
        _parse_deal_discount(raw)


@pytest.mark.parametrize(("raw", "expected"), [("99.99", Decimal("99.99")), ("99,99", Decimal("99.99"))])
def test_manual_price_filter_accepts_dot_and_comma(raw: str, expected: Decimal) -> None:
    assert _parse_deal_price(raw) == expected


@pytest.mark.parametrize("raw", ["0", "-1", "1000001", "NaN"])
def test_manual_price_filter_rejects_invalid_values(raw: str) -> None:
    with pytest.raises((ValueError, ArithmeticError)):
        _parse_deal_price(raw)


def test_deal_card_has_visual_separator_and_no_technical_values() -> None:
    rendered = text(
        "en",
        "deals_card",
        index=1,
        url="https://store.steampowered.com/app/1",
        name="Game",
        discount=50,
        current="10 PLN",
        regular="20 PLN",
        rating="",
        historical="",
    )
    assert "━━━━━━━━" in rendered
    assert not any(value in rendered for value in ("None", "null", "NaN"))


def test_admin_panel_is_grouped() -> None:
    values = callbacks(admin_keyboard("en"))
    assert values[:4] == ["admin:users", "admin:premium", "admin:broadcasts", "admin:giveaways"]
    assert "admin:deals_broadcast" not in values
    premium_values = callbacks(admin_premium_root_keyboard("en"))
    assert "admin_premium_users:0" in premium_values
    assert "admin:premium_search" in premium_values


def test_deals_sorting_has_no_unsupported_end_time_option() -> None:
    values = callbacks(deals_sort_keyboard("en", "discount"))
    assert "deals_sort:ending" not in values


def test_digest_navigation_stays_inside_digest_screens() -> None:
    menu_values = callbacks(digest_keyboard("en", True, True))
    assert "digest:guide" in menu_values
    assert "ui:close" not in menu_values
    daily_values = callbacks(digest_kind_keyboard("en", "daily", True))
    weekly_values = callbacks(digest_kind_keyboard("en", "weekly", True))
    assert "settings:digest" in daily_values and "menu:home" in daily_values
    assert "settings:digest" in weekly_values and "menu:home" in weekly_values


@pytest.mark.parametrize("language", ["ru", "en", "uk", "pl"])
def test_information_pages_avoid_internal_terms(language: str) -> None:
    for key in (
        "info_start_page",
        "info_search_page",
        "info_discounts_page",
        "info_giveaways_page",
        "info_analytics_page",
        "info_premium_page",
        "info_region_page",
    ):
        content = TEXTS[language][key]
        assert "App ID" not in content
        assert "IANA" not in content
        assert "API" not in content


@pytest.mark.asyncio
async def test_admin_user_selection_has_names_and_pagination() -> None:
    users = [
        SimpleNamespace(
            id=index,
            telegram_id=1000 + index,
            username=f"user{index}",
            display_name=None,
            is_premium=False,
            premium_until=None,
        )
        for index in range(1, 4)
    ]
    keyboard = await _admin_users_markup(users, "en", page=0, pages=2)
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert any("@user1" in label for label in labels)
    assert "admin_premium_users:1" in callbacks(keyboard)
