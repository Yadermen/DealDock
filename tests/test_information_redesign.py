import string

import pytest

from steam_radar.bot.handlers import _info_frequency
from steam_radar.bot.keyboards import info_keyboard, info_page_keyboard, premium_gate_keyboard
from steam_radar.constants import FREE_GAME_LIMIT, PREMIUM_GAME_LIMIT, PREMIUM_PRICES
from steam_radar.i18n import TEXTS, text

LANGUAGES = ("ru", "en", "uk", "pl")
PAGES = ("start", "search", "discounts", "giveaways", "analytics", "region", "premium", "plans", "referrals")


def callbacks(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data]


@pytest.mark.parametrize("language", LANGUAGES)
def test_information_root_opens_every_section(language: str) -> None:
    values = callbacks(info_keyboard(language))
    assert values == [
        "info:search",
        "info:discounts",
        "info:giveaways",
        "info:analytics",
        "info:region",
        "info:premium",
        "info:referrals",
        "info:start",
        "menu:home",
    ]


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("page", PAGES)
def test_information_pages_have_working_back_navigation(language: str, page: str) -> None:
    values = callbacks(info_page_keyboard(language, page))
    expected = "info:premium" if page == "plans" else "menu:info"
    assert expected in values
    assert "ui:close" not in values


@pytest.mark.parametrize("language", LANGUAGES)
def test_premium_page_returns_to_origin_and_active_user_has_no_purchase(language: str) -> None:
    free_values = callbacks(info_page_keyboard(language, "premium", premium_back="info:analytics"))
    active_values = callbacks(info_page_keyboard(language, "premium", is_premium=True, premium_back="info:analytics"))
    assert "info:analytics" in free_values
    assert any(value.startswith("buy:") for value in free_values)
    assert "info:analytics" in active_values
    assert not any(value.startswith("buy:") for value in active_values)
    assert "menu:premium" in active_values


def test_shared_premium_paywall_keeps_its_return_target() -> None:
    markup = premium_gate_keyboard("en", "info:giveaways", 12, PREMIUM_PRICES[12])
    assert "premium:info:info:giveaways" in callbacks(markup)
    assert "info:giveaways" in callbacks(markup)


@pytest.mark.parametrize("language", LANGUAGES)
def test_dynamic_plan_values_render_without_placeholders(language: str) -> None:
    values = {
        "free_limit": FREE_GAME_LIMIT,
        "premium_limit": PREMIUM_GAME_LIMIT,
        "free_frequency": _info_frequency(language, 24),
        "premium_frequency": _info_frequency(language, 1),
        "subscription_status": text(language, "info_subscription_free"),
        "plans": "\n".join(
            text(language, "premium_period", months=months, stars=stars) for months, stars in PREMIUM_PRICES.items()
        ),
    }
    for key in ("info_premium_page", "info_plans_page"):
        content = text(language, key, **values)
        assert str(FREE_GAME_LIMIT) in content or key == "info_premium_page"
        assert str(PREMIUM_GAME_LIMIT) in content
        assert len(content) <= 4096
        assert not any(value in content for value in ("None", "null", "NaN", "{", "}"))
        assert content.count("<b>") == content.count("</b>")


def test_information_localizations_have_identical_placeholders() -> None:
    formatter = string.Formatter()
    for page in PAGES:
        key = f"info_{page}_page"
        expected = {field for _, field, _, _ in formatter.parse(TEXTS["ru"][key]) if field}
        for language in LANGUAGES:
            value = TEXTS[language][key]
            assert {field for _, field, _, _ in formatter.parse(value) if field} == expected
            assert value.strip()


@pytest.mark.parametrize("language", LANGUAGES)
def test_referral_information_explains_link_activation_rewards_and_navigation(language: str) -> None:
    content = text(language, "info_referrals_page")
    for value in ("3", "5", "10", "25", "50", "90", "365"):
        assert value in content
    assert "DealDock" in content
    assert len(content) <= 4096
    assert content.count("<b>") == content.count("</b>")
    assert callbacks(info_page_keyboard(language, "referrals")) == ["menu:info"]
