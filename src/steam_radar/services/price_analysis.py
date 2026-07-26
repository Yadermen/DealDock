from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from html import escape

from steam_radar.i18n import text
from steam_radar.services.price_history import PriceHistoryPoint
from steam_radar.services.pricing import format_money


@dataclass(frozen=True, slots=True)
class PremiumPriceAnalysis:
    current_price: Decimal
    regular_price: Decimal | None
    current_discount: int
    historical_low: Decimal | None
    historical_low_at: datetime | None
    historical_low_discount: int | None
    low_difference: Decimal | None
    low_difference_percent: int | None
    score: int | None
    category_key: str
    explanation_keys: tuple[str, ...]
    recommendation_key: str
    period_change: Decimal | None
    maximum_discount: int | None
    average_discount: int | None
    minimum: Decimal | None
    maximum: Decimal | None
    average: Decimal | None
    discount_periods: int
    average_discount_days: int | None


class PriceAnalyticsService:
    def analyze(
        self,
        points: list[PriceHistoryPoint],
        current_price: Decimal,
        regular_price: Decimal | None,
        current_discount: int,
        target_price: Decimal | None,
        target_discount: int | None,
    ) -> PremiumPriceAnalysis:
        valid = [point for point in points if point.price >= 0]
        prices = [point.price for point in valid]
        historical = min(valid, key=lambda item: item.price) if valid else None
        minimum = min(prices) if prices else None
        maximum = max(prices) if prices else None
        average = sum(prices, Decimal(0)) / len(prices) if prices else None
        low_difference = current_price - historical.price if historical else None
        low_difference_percent = (
            int((low_difference / historical.price * 100).quantize(Decimal("1")))
            if historical and historical.price > 0 and low_difference is not None
            else None
        )
        discounts = [self._discount(point.price, point.regular_price) for point in valid]
        discounts = [value for value in discounts if value is not None and value > 0]
        maximum_discount = max(discounts, default=None)
        average_discount = round(sum(discounts) / len(discounts)) if discounts else None
        low_discount = self._discount(historical.price, historical.regular_price) if historical else None
        periods, average_days = self._discount_periods(valid)
        score = self._score(
            current_price,
            regular_price,
            current_discount,
            historical.price if historical else None,
            minimum,
            maximum,
            maximum_discount,
            target_price,
            target_discount,
        )
        category = self._category(score)
        explanations = self._explanations(
            current_price,
            historical.price if historical else None,
            current_discount,
            average_discount,
            target_price,
            target_discount,
        )
        return PremiumPriceAnalysis(
            current_price=current_price,
            regular_price=regular_price,
            current_discount=current_discount,
            historical_low=historical.price if historical else None,
            historical_low_at=historical.checked_at if historical else None,
            historical_low_discount=low_discount,
            low_difference=low_difference,
            low_difference_percent=low_difference_percent,
            score=score,
            category_key=category,
            explanation_keys=explanations,
            recommendation_key=self._recommendation(score),
            period_change=current_price - valid[0].price if valid else None,
            maximum_discount=maximum_discount,
            average_discount=average_discount,
            minimum=minimum,
            maximum=maximum,
            average=average,
            discount_periods=periods,
            average_discount_days=average_days,
        )

    @staticmethod
    def _discount(price: Decimal, regular: Decimal | None) -> int | None:
        if regular is None or regular <= 0 or price > regular:
            return None
        return int(((regular - price) / regular * 100).quantize(Decimal("1")))

    @staticmethod
    def _discount_periods(points: list[PriceHistoryPoint]) -> tuple[int, int | None]:
        starts: list[datetime] = []
        durations: list[int] = []
        active_since: datetime | None = None
        for point in points:
            discounted = point.regular_price is not None and point.price < point.regular_price
            if discounted and active_since is None:
                active_since = point.checked_at
                starts.append(point.checked_at)
            elif not discounted and active_since is not None:
                durations.append(max(1, (point.checked_at - active_since).days))
                active_since = None
        if active_since is not None:
            durations.append(max(1, (datetime.now(UTC) - active_since).days))
        return len(starts), round(sum(durations) / len(durations)) if durations else None

    def _score(
        self,
        current: Decimal,
        regular: Decimal | None,
        discount: int,
        historical_low: Decimal | None,
        minimum: Decimal | None,
        maximum: Decimal | None,
        maximum_discount: int | None,
        target_price: Decimal | None,
        target_discount: int | None,
    ) -> int | None:
        components: list[tuple[Decimal, Decimal]] = []
        if regular and regular > 0:
            components.append((Decimal(max(0, min(100, discount))), Decimal(25)))
        if historical_low and historical_low > 0:
            proximity = Decimal(100) - max(Decimal(0), (current - historical_low) / historical_low * 100)
            components.append((max(Decimal(0), min(Decimal(100), proximity)), Decimal(30)))
        if minimum is not None and maximum is not None and maximum > minimum:
            position = (maximum - current) / (maximum - minimum) * 100
            components.append((max(Decimal(0), min(Decimal(100), position)), Decimal(20)))
        if maximum_discount and maximum_discount > 0:
            components.append((min(Decimal(100), Decimal(discount) / maximum_discount * 100), Decimal(10)))
        targets: list[Decimal] = []
        if target_price is not None:
            targets.append(Decimal(100 if current <= target_price else 0))
        if target_discount is not None:
            targets.append(Decimal(100 if discount >= target_discount else 0))
        if targets:
            components.append((sum(targets) / len(targets), Decimal(15)))
        if len(components) < 2:
            return None
        weight = sum(item[1] for item in components)
        return max(0, min(100, int((sum(value * part for value, part in components) / weight).quantize(Decimal("1")))))

    @staticmethod
    def _category(score: int | None) -> str:
        if score is None:
            return "premium_value_unknown"
        if score >= 95:
            return "premium_value_best"
        if score >= 85:
            return "premium_value_excellent"
        if score >= 70:
            return "premium_value_good"
        if score >= 50:
            return "premium_value_normal"
        if score >= 30:
            return "premium_value_wait"
        return "premium_value_bad"

    @staticmethod
    def _recommendation(score: int | None) -> str:
        if score is None:
            return "premium_recommendation_unknown"
        if score >= 85:
            return "premium_recommendation_buy"
        if score >= 70:
            return "premium_recommendation_good"
        if score >= 50:
            return "premium_recommendation_neutral"
        return "premium_recommendation_wait"

    @staticmethod
    def _explanations(
        current: Decimal,
        low: Decimal | None,
        discount: int,
        average_discount: int | None,
        target_price: Decimal | None,
        target_discount: int | None,
    ) -> tuple[str, ...]:
        result: list[str] = []
        if low and current <= low:
            result.append("premium_explain_at_low")
        elif low and current <= low * Decimal("1.15"):
            result.append("premium_explain_near_low")
        elif low:
            result.append("premium_explain_above_low")
        if average_discount is not None:
            result.append(
                "premium_explain_discount_above" if discount >= average_discount else "premium_explain_discount_below"
            )
        if (target_price is not None and current <= target_price) or (
            target_discount is not None and discount >= target_discount
        ):
            result.append("premium_explain_target_met")
        return tuple(result[:2])

    def build_card(
        self,
        language: str,
        game_name: str,
        currency: str,
        analysis: PremiumPriceAnalysis,
        target_price: Decimal | None,
        target_discount: int | None,
    ) -> str:
        lines = [
            text(language, "premium_analysis_title", game=escape(game_name)),
            text(
                language,
                "premium_analysis_now",
                price=format_money(analysis.current_price, currency),
                discount=analysis.current_discount,
            ),
        ]
        if analysis.regular_price is not None and analysis.regular_price > analysis.current_price:
            lines.append(
                text(
                    language,
                    "premium_analysis_regular",
                    price=format_money(analysis.regular_price, currency),
                )
            )
        if analysis.historical_low is not None:
            low_discount = (
                f" · −{analysis.historical_low_discount}%" if analysis.historical_low_discount is not None else ""
            )
            date = analysis.historical_low_at.strftime("%d.%m.%Y") if analysis.historical_low_at else ""
            difference = (
                text(
                    language,
                    "premium_analysis_difference",
                    amount=format_money(analysis.low_difference, currency),
                    percent=analysis.low_difference_percent,
                )
                if analysis.low_difference is not None
                and analysis.low_difference > 0
                and analysis.low_difference_percent is not None
                else text(language, "premium_analysis_at_low")
            )
            lines.extend(
                [
                    "━━━━━━━━━━━━━━━━━━",
                    text(
                        language,
                        "premium_analysis_low",
                        price=format_money(analysis.historical_low, currency),
                        discount=low_discount,
                        date=date,
                        difference=difference,
                    ),
                ]
            )
        if analysis.score is not None and analysis.score > 0:
            explanations = "\n".join(text(language, key) for key in analysis.explanation_keys)
            lines.extend(
                [
                    "━━━━━━━━━━━━━━━━━━",
                    text(
                        language,
                        "premium_analysis_value",
                        score=analysis.score,
                        category=text(language, analysis.category_key),
                        explanation=explanations,
                    ),
                ]
            )
        lines.extend(
            [
                "━━━━━━━━━━━━━━━━━━",
                text(
                    language,
                    "premium_analysis_recommendation",
                    recommendation=text(language, analysis.recommendation_key),
                ),
            ]
        )
        return "\n\n".join(lines)

    def build_history_summary(
        self,
        language: str,
        currency: str,
        analysis: PremiumPriceAnalysis,
        period: str,
        source: str,
    ) -> str:
        if analysis.minimum is None or analysis.maximum is None or analysis.average is None:
            return text(language, "premium_history_insufficient")
        change = analysis.period_change
        change_label = (
            text(language, "premium_movement_same")
            if change == 0
            else (
                ("↓ " if change and change < 0 else "↑ ") + format_money(abs(change), currency)
                if change is not None
                else text(language, "premium_history_no_change_data")
            )
        )
        low_distance = (
            format_money(analysis.low_difference, currency)
            if analysis.low_difference is not None and analysis.low_difference > 0
            else text(language, "premium_analysis_at_low")
        )
        return text(
            language,
            "premium_history_summary",
            period=text(language, f"history_period_{period}"),
            current=format_money(analysis.current_price, currency),
            minimum=format_money(analysis.minimum, currency),
            maximum=format_money(analysis.maximum, currency),
            average=format_money(analysis.average, currency),
            maximum_discount=(
                f"−{analysis.maximum_discount}%" if analysis.maximum_discount is not None else text(language, "no_data")
            ),
            change=change_label,
            low_distance=low_distance,
            source=text(language, f"history_source_{source}"),
        )
