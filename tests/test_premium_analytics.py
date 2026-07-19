from decimal import Decimal

from steam_radar.services.premium_analytics import deal_label_key, price_analytics, sparkline


def test_price_analytics_uses_observed_history() -> None:
    result = price_analytics(
        [Decimal("100"), Decimal("80"), Decimal("50")],
        base_price=Decimal("120"),
    )
    assert result is not None
    assert result.minimum == Decimal("50")
    assert result.maximum == Decimal("100")
    assert result.average == Decimal("76.67")
    assert result.potential_saving == Decimal("70.00")
    assert result.score == 100
    assert deal_label_key(result.score, result.current, result.minimum) == "deal_best"


def test_price_analytics_rejects_empty_history() -> None:
    assert price_analytics([]) is None
    assert sparkline([]) == "—"


def test_single_observation_is_not_called_a_deal_or_trend() -> None:
    result = price_analytics([Decimal("10500")])
    assert result is not None
    assert result.score is None
    assert result.trend == "—"
    assert result.direction_key == "trend_insufficient"
    assert deal_label_key(result.score, result.current, result.minimum) == "deal_insufficient"


def test_sparkline_tracks_direction() -> None:
    graph = sparkline([Decimal("1"), Decimal("2"), Decimal("3")])
    assert len(graph) == 3
    assert graph[0] < graph[-1]


def test_deal_score_uses_observed_range() -> None:
    minimum = price_analytics([Decimal("100"), Decimal("50")])
    maximum = price_analytics([Decimal("50"), Decimal("100")])
    unchanged = price_analytics([Decimal("50"), Decimal("50")])
    assert minimum and minimum.score == 100
    assert maximum and maximum.score == 0
    assert deal_label_key(maximum.score, maximum.current, maximum.minimum) == "deal_maximum"
    assert unchanged and unchanged.score is None
    assert deal_label_key(unchanged.score, unchanged.current, unchanged.minimum) == "deal_insufficient"
