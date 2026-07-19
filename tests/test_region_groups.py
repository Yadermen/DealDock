import inspect

from steam_radar.bot.handlers import change_region
from steam_radar.bot.keyboards import (
    comparison_regions_keyboard,
    region_groups_keyboard,
    region_keyboard,
    timezone_groups_keyboard,
    timezone_keyboard,
)
from steam_radar.constants import MAX_COMPARISON_REGIONS, REGION_GROUPS, REGIONS


def test_every_region_belongs_to_a_known_group() -> None:
    assert REGIONS
    assert all(region.geo_group in REGION_GROUPS for region in REGIONS.values())
    keyboard = region_groups_keyboard("en")
    callbacks = {button.callback_data for row in keyboard.inline_keyboard for button in row}
    assert {f"region_group:{group}" for group in REGION_GROUPS}.issubset(callbacks)


def test_region_keyboard_only_contains_selected_group() -> None:
    keyboard = comparison_regions_keyboard("en", "asia", set())
    callbacks = {button.callback_data for row in keyboard.inline_keyboard for button in row if button.callback_data}
    selected = {item.split(":", 1)[1] for item in callbacks if item.startswith("compare_toggle:")}
    assert selected
    assert all(REGIONS[code].geo_group == "asia" for code in selected)


def test_region_change_does_not_overwrite_timezone() -> None:
    source = inspect.getsource(change_region)
    assert "user.timezone =" not in source
    assert MAX_COMPARISON_REGIONS == 10


def test_region_and_timezone_choices_are_grouped_and_compact() -> None:
    region_rows = region_keyboard("en", group="europe").inline_keyboard
    assert any(len(row) >= 2 for row in region_rows)
    assert not any("·" in button.text for row in region_rows for button in row)
    group_callbacks = {button.callback_data for row in timezone_groups_keyboard("en").inline_keyboard for button in row}
    assert "timezone_group:europe" in group_callbacks
    zone_rows = timezone_keyboard("en", "asia").inline_keyboard
    assert any(button.callback_data == "timezone:Asia/Tokyo" for row in zone_rows for button in row)
