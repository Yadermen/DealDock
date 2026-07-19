from datetime import UTC, datetime

from steam_radar.bot.handlers import _updated_label


def test_giveaway_update_time_contains_timezone_and_offset() -> None:
    value = datetime(2026, 7, 18, 12, 0, tzinfo=UTC).isoformat()
    label = _updated_label(value, "Europe/Warsaw")
    assert "CEST" in label
    assert "UTC+02:00" in label
