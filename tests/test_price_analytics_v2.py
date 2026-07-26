import inspect
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from steam_radar.bot.handlers import premium_game_analytics, premium_price_history
from steam_radar.bot.keyboards import price_history_keyboard
from steam_radar.i18n import TEXTS
from steam_radar.services.itad import ITADHistoryPoint
from steam_radar.services.price_analysis import PriceAnalyticsService
from steam_radar.services.price_chart import PriceChartService
from steam_radar.services.price_history import PriceHistoryPoint, PriceHistoryResult, PriceHistoryService


class MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, **_: object) -> None:
        self.values[key] = value


def _history() -> list[PriceHistoryPoint]:
    now = datetime.now(UTC)
    return [
        PriceHistoryPoint(now - timedelta(days=100), Decimal("100"), Decimal("100"), "PLN"),
        PriceHistoryPoint(now - timedelta(days=60), Decimal("60"), Decimal("100"), "PLN"),
        PriceHistoryPoint(now - timedelta(days=30), Decimal("80"), Decimal("100"), "PLN"),
        PriceHistoryPoint(now, Decimal("70"), Decimal("100"), "PLN"),
    ]


def test_premium_analysis_has_bounded_score_and_no_internal_observation_data() -> None:
    service = PriceAnalyticsService()
    analysis = service.analyze(_history(), Decimal("70"), Decimal("100"), 30, Decimal("75"), 25)
    assert analysis.score is not None
    assert 0 <= analysis.score <= 100
    card = service.build_card("en", "Example", "PLN", analysis, Decimal("75"), 25)
    assert "None" not in card and re.search(r"(?<!\d)0/100", card) is None
    assert "observations" not in card.lower() and "updated" not in card.lower()
    assert "Historical low" in card
    assert "Your targets" not in card
    assert "Discount history" not in card
    assert "Dynamics" not in card


def test_identical_consecutive_prices_are_collapsed() -> None:
    points = _history()
    duplicate = PriceHistoryPoint(
        points[-1].checked_at + timedelta(hours=1),
        points[-1].price,
        points[-1].regular_price,
        "PLN",
    )
    assert len(PriceHistoryService._collapse(points + [duplicate])) == len(points)


@pytest.mark.asyncio
async def test_price_history_uses_redis_before_external_or_database() -> None:
    class ForbiddenFactory:
        def __call__(self):
            raise AssertionError("database must not be used on a Redis hit")

    redis = MemoryRedis()
    encoded = PriceHistoryService._encode(PriceHistoryResult(_history(), "isthereanydeal"))
    redis.values["price-history:v2:1:PL:PLN"] = encoded
    result = await PriceHistoryService(None, ForbiddenFactory(), redis).get_history(1, 10, "PL", "PLN", None)  # type: ignore[arg-type]
    assert result.source == "isthereanydeal"
    assert len(result.points) == 4


@pytest.mark.asyncio
async def test_external_history_is_converted_to_user_currency() -> None:
    class Currency:
        async def rates(self):
            return SimpleNamespace(rates={"USD": Decimal("1"), "KZT": Decimal("500")})

    service = PriceHistoryService(None, None, MemoryRedis(), Currency())  # type: ignore[arg-type]
    rows = [
        ITADHistoryPoint(
            datetime(2026, 1, 1, tzinfo=UTC),
            Decimal("10"),
            Decimal("20"),
            "USD",
        )
    ]
    points = await service._external_points(rows, "KZT")
    assert points[0].price == Decimal("5000.00")
    assert points[0].regular_price == Decimal("10000.00")
    assert points[0].currency == "KZT"


def test_short_period_keeps_boundary_price_for_chart() -> None:
    now = datetime.now(UTC)
    result = PriceHistoryResult(
        [
            PriceHistoryPoint(now - timedelta(days=40), Decimal("100"), Decimal("100"), "PLN"),
            PriceHistoryPoint(now - timedelta(days=2), Decimal("70"), Decimal("100"), "PLN"),
        ],
        "isthereanydeal",
    )
    filtered = PriceHistoryService._filter(result, 7, "PLN")
    assert len(filtered.points) == 2
    assert filtered.points[0].price == Decimal("100")


@pytest.mark.asyncio
async def test_dark_price_chart_is_png_and_cached() -> None:
    redis = MemoryRedis()
    service = PriceChartService(redis)  # type: ignore[arg-type]
    labels = {
        "title": "Price history",
        "price": "Price",
        "regular": "Regular",
        "low": "Low",
        "target": "Target",
        "axis_price": "Price, PLN",
        "locale": "en",
    }
    points = _history()
    first = await service.render(points, "PLN", Decimal("100"), Decimal("60"), Decimal("75"), labels, "game:30")
    second = await service.render(points, "PLN", Decimal("100"), Decimal("60"), Decimal("75"), labels, "game:30")
    assert first is not None and first.startswith(b"\x89PNG\r\n\x1a\n")
    assert second == first
    assert len(redis.values) == 1


def test_chart_points_are_sorted_cleaned_and_repeated_runs_reduced() -> None:
    now = datetime.now(UTC)
    points = [
        PriceHistoryPoint(now, Decimal("70"), Decimal("100"), "PLN"),
        PriceHistoryPoint(now - timedelta(days=3), Decimal("100"), Decimal("100"), "PLN"),
        PriceHistoryPoint(now - timedelta(days=2), Decimal("100"), Decimal("NaN"), "PLN"),
        PriceHistoryPoint(now - timedelta(days=1), Decimal("100"), Decimal("100"), "PLN"),
        PriceHistoryPoint(now - timedelta(hours=12), Decimal("-1"), Decimal("100"), "PLN"),
        PriceHistoryPoint(now, Decimal("70"), Decimal("100"), "PLN"),
    ]
    prepared = PriceChartService._prepare_points(points)
    assert [point.checked_at for point in prepared] == sorted(point.checked_at for point in prepared)
    assert [point.price for point in prepared] == [Decimal("100"), Decimal("100"), Decimal("70")]
    assert prepared[0].regular_price == Decimal("100")
    assert all(point.price.is_finite() and point.price >= 0 for point in prepared)


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [7, 30, 90, 365, 1500])
async def test_chart_renders_short_and_long_periods(days: int) -> None:
    redis = MemoryRedis()
    now = datetime.now(UTC)
    points = [
        PriceHistoryPoint(
            now - timedelta(days=days - index * days / 12),
            Decimal(100 - index * 2),
            Decimal("100"),
            "PLN",
        )
        for index in range(12)
    ]
    chart = await PriceChartService(redis).render(  # type: ignore[arg-type]
        points,
        "PLN",
        Decimal("100"),
        Decimal("78"),
        Decimal("80"),
        {
            "title": "Price history",
            "price": "Price",
            "regular": "Regular",
            "low": "Low",
            "target": "Target",
            "axis_price": "Price, PLN",
            "locale": "en",
        },
        f"period:{days}",
    )
    assert chart is not None and chart.startswith(b"\x89PNG\r\n\x1a\n")


def test_chart_uses_chronological_step_segments_without_fill() -> None:
    source = inspect.getsource(PriceChartService._render_sync)
    assert "axis.step(" in source
    assert 'where="post"' in source
    assert "fill_between" not in source


def test_chart_legend_wraps_and_is_placed_outside_plot_area() -> None:
    assert PriceChartService._legend_columns(["Price", "Regular price", "Historical low"]) == 3
    assert PriceChartService._legend_columns(
        ["Bardzo długa cena", "Bardzo długa cena regularna", "Bardzo długie minimum historyczne"]
    ) == 2
    assert PriceChartService._legend_columns(["Price", "Low"]) == 2
    source = inspect.getsource(PriceChartService._render_sync)
    assert "figure.legend(" in source
    assert "bbox_to_anchor=(0.53, 0.89)" in source
    assert "subplots_adjust" in source


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("ru", "7\u00a0499,00 KZT"),
        ("en", "7,499.00 KZT"),
        ("pl", "7\u00a0499,00 KZT"),
        ("uk", "7\u00a0499,00 KZT"),
    ],
)
def test_chart_price_format_is_localized(locale: str, expected: str) -> None:
    assert PriceChartService._format_price(Decimal("7499"), "KZT", locale) == expected


def test_chart_y_axis_is_non_negative_and_date_offset_is_hidden() -> None:
    source = inspect.getsource(PriceChartService._render_sync)
    assert "max(0.0, minimum_value - padding)" in source
    assert "show_offset=False" in source


def test_price_history_updates_existing_photo_with_edit_media() -> None:
    source = inspect.getsource(premium_price_history)
    assert ".edit_media(" in source
    assert "price_history:message:" in source
    assert "price_history_screen_failed" in source
    assert "premium_history_loading" in source
    assert "latest.currency,\n        analysis,\n        period_key,\n        history.source," in source


def test_price_history_callback_is_not_claimed_by_analytics_handler() -> None:
    analytics_source = inspect.getsource(premium_game_analytics)
    history_source = inspect.getsource(premium_price_history)
    assert 'F.data.startswith("price_history:")' not in analytics_source
    assert 'F.data.startswith("price_history:")' in history_source


def test_price_history_keyboard_has_every_period_and_back() -> None:
    keyboard = price_history_keyboard("en", 42, "30")
    callbacks = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    }
    assert {f"price_history:42:{period}" for period in ("7", "30", "90", "365", "all")} <= callbacks
    assert "premium_analytics:42" in callbacks
    active = [
        button.text
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data == "price_history:42:30"
    ]
    assert active[0].startswith("✅ ")


@pytest.mark.parametrize("language", ["ru", "en", "pl", "uk"])
def test_history_summary_accepts_analysis_before_period_for_every_locale(language: str) -> None:
    service = PriceAnalyticsService()
    analysis = service.analyze(_history(), Decimal("70"), Decimal("100"), 30, None, None)
    summary = service.build_history_summary(language, "PLN", analysis, "30", "isthereanydeal")
    assert "None" not in summary
    assert "30" in summary


@pytest.mark.parametrize("language", ["ru", "en", "pl", "uk"])
def test_premium_card_has_no_dynamics_block(language: str) -> None:
    service = PriceAnalyticsService()
    analysis = service.analyze(_history(), Decimal("70"), Decimal("100"), 30, None, None)
    card = service.build_card(language, "Example", "PLN", analysis, None, None)
    assert TEXTS[language]["premium_analysis_dynamics"].split("{", 1)[0].strip() not in card
