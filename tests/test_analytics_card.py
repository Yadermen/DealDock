from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from steam_radar.i18n import text
from steam_radar.services.analytics import AnalyticsSnapshot, build_price_analytics_card


def snapshot(price: str, base: str | None, days: int = 0) -> AnalyticsSnapshot:
    now = datetime(2026, 7, 19, 15, 47, tzinfo=UTC)
    return AnalyticsSnapshot(
        Decimal(price),
        Decimal(base) if base else None,
        "PLN",
        now - timedelta(days=days),
    )


@pytest.mark.parametrize("language", ["ru", "en", "uk", "pl"])
def test_external_low_is_distinct_and_no_technical_values(language: str) -> None:
    now = datetime(2026, 7, 19, 15, 47, tzinfo=UTC)
    card = build_price_analytics_card(
        language,
        "Rust",
        [snapshot("99.99", "199.99", 3), snapshot("119.99", "199.99")],
        Decimal("79.99"),
        "PLN",
        now,
        now,
    )
    assert "79.99 PLN" in card.content
    assert text(language, "analytics_observed_min", value="99.99 PLN") not in card.content
    assert all(value not in card.content for value in ("None", "null", "NaN", "None/100"))


def test_observed_statistics_are_in_separate_observation_block() -> None:
    now = datetime(2026, 7, 19, 15, 47, tzinfo=UTC)
    snapshots = [snapshot("99.99", "199.99", 3), snapshot("119.99", "199.99")]
    card = build_price_analytics_card("en", "Rust", snapshots, Decimal("79.99"), "PLN", now, now)
    assert "Steam Radar observation" in card.content
    assert "period low" in card.content
    assert "average price" in card.content
    assert "tracked since" in card.content


def test_observed_minimum_remains_distinct_without_external_low() -> None:
    now = datetime(2026, 7, 19, 15, 47, tzinfo=UTC)
    card = build_price_analytics_card(
        "en", "Rust", [snapshot("99.99", "199.99", 3), snapshot("119.99", "199.99")], None, None, None, now
    )
    assert "period low" in card.content
    assert "Historical low" not in card.content


def test_missing_external_low_and_single_observation_hide_score() -> None:
    now = datetime(2026, 7, 19, 15, 47, tzinfo=UTC)
    card = build_price_analytics_card("en", "Rust", [snapshot("119.99", None)], None, None, None, now)
    assert "/100" not in card.content
    assert "Not enough price changes" in card.content


def test_current_equal_historical_low_is_excellent() -> None:
    now = datetime(2026, 7, 19, 15, 47, tzinfo=UTC)
    card = build_price_analytics_card(
        "en",
        "Rust",
        [snapshot("100", "200", 1), snapshot("80", "200")],
        Decimal("80"),
        "PLN",
        now,
        now,
    )
    assert "Excellent price" in card.content
    assert "🏆 Historical low" in card.content


def test_empty_local_history_is_safe() -> None:
    card = build_price_analytics_card("en", "Rust", [], Decimal("80"), "PLN", None, datetime(2026, 7, 19, tzinfo=UTC))
    assert "No data" in card.content and "/100" not in card.content
