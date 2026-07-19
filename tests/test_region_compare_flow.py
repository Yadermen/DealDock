from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from steam_radar.bot.handlers import _comparison_rule_error, _parse_compare_callback
from steam_radar.bot.keyboards import comparison_groups_keyboard, comparison_regions_keyboard
from steam_radar.constants import REGION_GROUPS
from steam_radar.i18n import TEXTS, text


@pytest.mark.parametrize(
    ("callback_data", "expected"),
    [
        ("premium_compare:42", ("rule", 42)),
        ("premium_compare:rule:42", ("rule", 42)),
        ("premium_compare:steam:252490", ("steam", 252490)),
    ],
)
def test_compare_callback_parsing(callback_data: str, expected: tuple[str, int]) -> None:
    assert _parse_compare_callback(callback_data) == expected


@pytest.mark.parametrize(
    "callback_data", ["premium_compare:", "premium_compare:x:1", "old:1", "premium_compare:rule:x"]
)
def test_stale_or_malformed_compare_callback_is_rejected(callback_data: str) -> None:
    with pytest.raises(ValueError):
        _parse_compare_callback(callback_data)


def _premium_user(user_id: int = 1):
    return SimpleNamespace(
        id=user_id,
        is_premium=True,
        premium_until=datetime.now(UTC) + timedelta(days=1),
    )


def _rule(user_id: int = 1, app_id: int | None = 252490):
    return SimpleNamespace(user_id=user_id, game=SimpleNamespace(steam_app_id=app_id))


def test_existing_owned_game_can_open_comparison() -> None:
    assert _comparison_rule_error(_premium_user(), _rule()) is None


def test_compare_game_not_found_other_owner_and_missing_app_id() -> None:
    assert _comparison_rule_error(_premium_user(), None) == "compare_game_missing"
    assert _comparison_rule_error(_premium_user(), _rule(user_id=2)) == "compare_not_owned"
    assert _comparison_rule_error(_premium_user(), _rule(app_id=None)) == "compare_app_id_missing"


def test_compare_requires_active_premium() -> None:
    user = _premium_user()
    user.is_premium = False
    assert _comparison_rule_error(user, _rule()) == "premium_required"


def test_comparison_group_keyboard_has_menu_no_close_and_compact_regions() -> None:
    groups = comparison_groups_keyboard("ru", 0, "USD")
    callbacks = [button.callback_data for row in groups.inline_keyboard for button in row]
    assert {f"compare_group:{group}" for group in REGION_GROUPS}.issubset(callbacks)
    assert "menu:home" in callbacks
    assert "ui:close" not in callbacks

    regions = comparison_regions_keyboard("en", "europe", set())
    assert all(len(row) <= 2 for row in regions.inline_keyboard)
    region_callbacks = [button.callback_data for row in regions.inline_keyboard for button in row]
    assert "ui:close" not in region_callbacks


@pytest.mark.parametrize("language", ["ru", "en", "uk", "kk", "pl"])
def test_comparison_screen_is_localized(language: str) -> None:
    rendered = text(language, "compare_setup_screen", game="Rust", selected=0)
    assert "Rust" in rendered
    assert "{game}" not in rendered
    assert TEXTS[language]["compare_minimum_two"].strip()
