from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from html import escape

from steam_radar.i18n import text
from steam_radar.services.premium_analytics import price_analytics
from steam_radar.services.pricing import format_money


@dataclass(frozen=True, slots=True)
class AnalyticsSnapshot:
    final_price: Decimal
    initial_price: Decimal | None
    currency: str
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class AnalyticsCard:
    content: str
    score: int | None
    assessment_key: str


def calculate_purchase_score(
    snapshots: list[AnalyticsSnapshot],
    external_low: Decimal | None,
    external_currency: str | None,
    now: datetime,
) -> int | None:
    if not snapshots:
        return None
    latest = snapshots[-1]
    history = [item for item in snapshots if item.currency == latest.currency and item.final_price >= 0]
    if (
        latest.final_price <= 0
        or latest.initial_price is None
        or latest.initial_price <= latest.final_price
        or now - latest.checked_at > timedelta(hours=48)
    ):
        return None
    components: list[tuple[Decimal, Decimal]] = [
        (max(Decimal(0), (latest.initial_price - latest.final_price) / latest.initial_price * 100), Decimal(45))
    ]
    has_external = external_low is not None and external_currency == latest.currency and external_low > 0
    if has_external:
        proximity = Decimal(100) - max(Decimal(0), (latest.final_price - external_low) / external_low * 100)
        components.append((max(Decimal(0), min(Decimal(100), proximity)), Decimal(30)))
    if len(history) >= 2:
        prices = [item.final_price for item in history]
        minimum, maximum = min(prices), max(prices)
        if maximum > minimum:
            position = (maximum - latest.final_price) / (maximum - minimum) * 100
            components.append((max(Decimal(0), min(Decimal(100), position)), Decimal(10)))
        trend = Decimal(100 if prices[-1] < prices[-2] else 20 if prices[-1] > prices[-2] else 50)
        components.extend([(trend, Decimal(10)), (min(Decimal(100), Decimal(len(prices) * 20)), Decimal(5))])
    if not has_external and len(history) < 2:
        return None
    total_weight = sum(weight for _, weight in components)
    score = sum(value * weight for value, weight in components) / total_weight
    return max(0, min(100, int(score.quantize(Decimal("1")))))


def build_price_analytics_card(
    language: str,
    game_name: str,
    snapshots: list[AnalyticsSnapshot],
    external_low: Decimal | None,
    external_currency: str | None,
    external_updated_at: datetime | None,
    now: datetime,
    *,
    detailed: bool = False,
) -> AnalyticsCard:
    valid = [item for item in snapshots if item.final_price >= 0 and item.currency]
    if not valid:
        content = text(language, "analytics_premium_title", game=escape(game_name))
        content += "\n\n" + text(language, "analytics_current_block", value=text(language, "no_data"))
        content += "\n\n" + text(language, "analytics_assessment_insufficient")
        return AnalyticsCard(content, None, "analytics_assessment_insufficient")
    latest = valid[-1]
    same_currency = [item for item in valid if item.currency == latest.currency]
    stats = price_analytics([item.final_price for item in same_currency], latest.initial_price)
    historical = external_low if external_currency == latest.currency and external_low is not None else None
    lines = [
        text(language, "analytics_title_new", game=escape(game_name)),
        text(language, "analytics_now_new", value=format_money(latest.final_price, latest.currency)),
    ]
    if latest.initial_price is not None and latest.initial_price > 0:
        discount = max(
            Decimal(0),
            (latest.initial_price - latest.final_price) / latest.initial_price * 100,
        ).quantize(Decimal("1"))
        lines.append(
            text(
                language,
                "analytics_regular_new",
                value=format_money(latest.initial_price, latest.currency),
                discount=discount,
            )
        )
    if historical is not None:
        difference = latest.final_price - historical
        comparison = (
            text(language, "analytics_historical_at")
            if difference <= 0
            else text(language, "analytics_historical_above", value=format_money(difference, latest.currency))
        )
        lines.append(
            text(
                language,
                "analytics_historical_new",
                value=format_money(historical, latest.currency),
                comparison=comparison,
            )
        )
    score = calculate_purchase_score(
        same_currency, historical, latest.currency if historical is not None else None, now
    )
    if score is None:
        assessment_key = "analytics_assessment_insufficient"
    elif historical is not None and latest.final_price <= historical:
        assessment_key = "analytics_assessment_excellent"
    elif score >= 75:
        assessment_key = "analytics_assessment_good"
    elif score >= 55:
        assessment_key = "analytics_assessment_normal"
    else:
        assessment_key = "analytics_assessment_wait"
    if score is not None:
        lines.append(text(language, "analytics_value_new", score=score, verdict=text(language, assessment_key)))
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append(text(language, "analytics_observation_title"))
    if stats and stats.sample_count >= 2 and stats.minimum != stats.maximum:
        observation_items = [
            text(language, "analytics_observation_count", value=stats.sample_count),
            text(language, "analytics_observation_since", value=same_currency[0].checked_at.strftime("%d.%m.%Y")),
            text(language, "analytics_observation_min", value=format_money(stats.minimum, latest.currency)),
            text(language, "analytics_observation_average", value=format_money(stats.average, latest.currency)),
            text(language, "analytics_observation_trend", value=text(language, stats.direction_key)),
        ]
        if stats.maximum != latest.initial_price:
            observation_items.insert(
                3,
                text(language, "analytics_observation_max", value=format_money(stats.maximum, latest.currency)),
            )
        lines.append("\n".join(observation_items))
    else:
        lines.append(text(language, "analytics_observation_insufficient"))
        lines.append(text(language, "analytics_observation_count", value=len(same_currency)))
        lines.append(
            text(language, "analytics_observation_since", value=same_currency[0].checked_at.strftime("%d.%m.%Y"))
        )
    updated = latest.checked_at.strftime("%d.%m.%Y, %H:%M %Z")
    lines.append(text(language, "analytics_updated", value=updated))
    return AnalyticsCard("\n\n".join(lines), score, assessment_key)
