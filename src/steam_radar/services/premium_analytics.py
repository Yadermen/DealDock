from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PriceAnalytics:
    current: Decimal
    minimum: Decimal
    maximum: Decimal
    average: Decimal
    potential_saving: Decimal
    sample_count: int
    score: int | None
    trend: str
    direction_key: str


def price_analytics(prices: list[Decimal], base_price: Decimal | None = None) -> PriceAnalytics | None:
    """Build honest analytics from prices observed by Steam Radar, not global history."""
    valid = [Decimal(value) for value in prices if value is not None and Decimal(value) >= 0]
    if not valid:
        return None
    current = valid[-1]
    minimum, maximum = min(valid), max(valid)
    average = (sum(valid) / len(valid)).quantize(Decimal("0.01"))
    reference = base_price if base_price is not None and base_price >= current else maximum
    saving = max(Decimal(0), reference - current).quantize(Decimal("0.01"))
    score = None
    if len(valid) >= 2 and maximum != minimum:
        score = int(((maximum - current) / (maximum - minimum) * 100).quantize(Decimal("1")))
    return PriceAnalytics(
        current=current,
        minimum=minimum,
        maximum=maximum,
        average=average,
        potential_saving=saving,
        sample_count=len(valid),
        score=max(0, min(100, score)) if score is not None else None,
        trend=sparkline(valid),
        direction_key=trend_direction_key(valid),
    )


def sparkline(values: list[Decimal]) -> str:
    if len(values) < 2:
        return "—"
    blocks = "▁▂▃▄▅▆▇█"
    low, high = min(values), max(values)
    if high == low:
        return blocks[3] * len(values)
    return "".join(blocks[min(7, int((value - low) / (high - low) * 7))] for value in values)


def trend_direction_key(values: list[Decimal]) -> str:
    if len(values) < 2:
        return "trend_insufficient"
    if values[-1] < values[0]:
        return "trend_down"
    if values[-1] > values[0]:
        return "trend_up"
    return "trend_flat"


def deal_label_key(score: int | None, current: Decimal, minimum: Decimal) -> str:
    if current == 0:
        return "deal_free"
    if score is None:
        return "deal_insufficient"
    if current == minimum:
        return "deal_best"
    if score >= 75:
        return "deal_great"
    if score >= 40:
        return "deal_good"
    if score == 0:
        return "deal_maximum"
    return "deal_weak"
