import asyncio
import base64
import os
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from io import BytesIO

from redis.asyncio import Redis

from steam_radar.services.price_history import PriceHistoryPoint


class PriceChartService:
    CACHE_TTL_SECONDS = 6 * 60 * 60

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def render(
        self,
        points: list[PriceHistoryPoint],
        currency: str,
        regular_price: Decimal | None,
        historical_low: Decimal | None,
        target_price: Decimal | None,
        labels: dict[str, str],
        cache_identity: str,
    ) -> bytes | None:
        points = self._prepare_points(points)
        if not points:
            return None
        if len(points) == 1:
            endpoint = max(datetime.now(UTC), points[0].checked_at + timedelta(days=1))
            points = [
                points[0],
                PriceHistoryPoint(endpoint, points[0].price, points[0].regular_price, points[0].currency),
            ]
        fingerprint = sha256(
            (
                cache_identity
                + "|"
                + "|".join(f"{point.checked_at.isoformat()}:{point.price}" for point in points)
                + f"|{regular_price}|{historical_low}|{target_price}|{labels.get('locale')}"
            ).encode()
        ).hexdigest()[:24]
        cache_key = f"price-chart:v2:{fingerprint}"
        if cached := await self.redis.get(cache_key):
            return base64.b64decode(cached)
        image = await asyncio.to_thread(
            self._render_sync,
            points,
            currency,
            regular_price,
            historical_low,
            target_price,
            labels,
        )
        await self.redis.set(cache_key, base64.b64encode(image).decode(), ex=self.CACHE_TTL_SECONDS)
        return image

    @staticmethod
    def _prepare_points(points: list[PriceHistoryPoint]) -> list[PriceHistoryPoint]:
        """Sort, validate and reduce noisy repeats while preserving real price changes."""
        by_timestamp: dict[datetime, PriceHistoryPoint] = {}
        for point in points:
            checked_at = point.checked_at
            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=UTC)
            if not point.price.is_finite() or point.price < 0:
                continue
            regular = point.regular_price
            if regular is not None and (not regular.is_finite() or regular <= 0):
                regular = None
            by_timestamp[checked_at] = PriceHistoryPoint(
                checked_at=checked_at,
                price=point.price,
                regular_price=regular,
                currency=point.currency,
            )
        ordered = [by_timestamp[key] for key in sorted(by_timestamp)]
        if len(ordered) < 2:
            return ordered

        reduced: list[PriceHistoryPoint] = [ordered[0]]
        for index, point in enumerate(ordered[1:], 1):
            previous = ordered[index - 1]
            next_point = ordered[index + 1] if index + 1 < len(ordered) else None
            price_changes_after = next_point is not None and next_point.price != point.price
            is_latest = next_point is None
            if point.price != previous.price or price_changes_after or is_latest:
                if reduced[-1].checked_at == point.checked_at:
                    reduced[-1] = point
                else:
                    reduced.append(point)
        return reduced

    @staticmethod
    def _legend_columns(labels: list[str]) -> int:
        if not labels:
            return 1
        total_width = sum(len(label) for label in labels)
        return len(labels) if len(labels) <= 3 and total_width <= 55 else min(2, len(labels))

    @staticmethod
    def _format_price(value: Decimal | float, currency: str, locale: str) -> str:
        rendered = f"{Decimal(str(value)):,.2f}"
        if locale != "en":
            rendered = rendered.replace(",", "\u00a0").replace(".", ",")
        return f"{rendered} {currency}"

    @staticmethod
    def _render_sync(
        points: list[PriceHistoryPoint],
        currency: str,
        regular_price: Decimal | None,
        historical_low: Decimal | None,
        target_price: Decimal | None,
        labels: dict[str, str],
    ) -> bytes:
        matplotlib_cache = os.path.join(tempfile.gettempdir(), "dealdock-matplotlib")
        os.makedirs(matplotlib_cache, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", matplotlib_cache)
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
        from matplotlib.ticker import FuncFormatter

        dates = [point.checked_at for point in points]
        prices = [float(point.price) for point in points]
        locale = labels.get("locale", "en")
        figure, axis = plt.subplots(figsize=(7.2, 4.6), dpi=160)
        figure.patch.set_facecolor("#111827")
        axis.set_facecolor("#111827")
        axis.step(
            dates,
            prices,
            where="post",
            color="#38BDF8",
            linewidth=2.4,
            solid_capstyle="round",
            solid_joinstyle="round",
            label=labels["price"],
        )
        if regular_price is not None and regular_price > 0:
            axis.axhline(
                float(regular_price),
                color="#94A3B8",
                linewidth=1.2,
                linestyle=(0, (5, 5)),
                label=labels["regular"],
            )
        if historical_low is not None:
            axis.axhline(
                float(historical_low),
                color="#22C55E",
                linewidth=1.5,
                alpha=0.9,
                label=labels["low"],
            )
            low_point = min(points, key=lambda point: abs(point.price - historical_low))
            axis.scatter([low_point.checked_at], [float(historical_low)], s=48, color="#22C55E", zorder=5)
            same_as_current = (
                low_point.checked_at == points[-1].checked_at
                and historical_low == points[-1].price
            )
            axis.annotate(
                PriceChartService._format_price(historical_low, currency, locale),
                (low_point.checked_at, float(historical_low)),
                xytext=(-10, -30 if same_as_current else -22),
                textcoords="offset points",
                ha="right" if same_as_current else "left",
                va="top",
                color="#86EFAC",
                fontsize=8,
                bbox={"boxstyle": "round,pad=0.25", "facecolor": "#111827", "edgecolor": "none", "alpha": 0.9},
            )
        if target_price is not None:
            axis.axhline(
                float(target_price),
                color="#F59E0B",
                linewidth=1.2,
                linestyle=(0, (3, 4)),
                label=labels["target"],
            )
        axis.scatter([dates[-1]], [prices[-1]], s=58, color="#F8FAFC", edgecolor="#38BDF8", zorder=6)
        axis.annotate(
            PriceChartService._format_price(points[-1].price, currency, locale),
            (dates[-1], prices[-1]),
            xytext=(-10, 18),
            textcoords="offset points",
            ha="right",
            va="bottom",
            color="#F8FAFC",
            fontsize=9,
            fontweight="bold",
            bbox={"boxstyle": "round,pad=0.3", "facecolor": "#0F172A", "edgecolor": "#38BDF8", "alpha": 0.9},
        )
        axis.set_ylabel(labels["axis_price"], color="#CBD5E1", fontsize=9)
        axis.grid(axis="y", color="#334155", alpha=0.45, linewidth=0.8)
        axis.spines[["top", "right", "left"]].set_visible(False)
        axis.spines["bottom"].set_color("#475569")
        axis.tick_params(colors="#94A3B8", labelsize=8)
        locator = mdates.AutoDateLocator(minticks=3, maxticks=6, interval_multiples=True)
        axis.xaxis.set_major_locator(locator)
        date_formatter = mdates.ConciseDateFormatter(locator, show_offset=False)
        axis.xaxis.set_major_formatter(date_formatter)
        axis.yaxis.set_major_formatter(
            FuncFormatter(
                lambda value, _position: PriceChartService._format_price(
                    value,
                    "",
                    locale,
                ).strip()
            )
        )
        range_values = list(prices)
        range_values.extend(
            float(value)
            for value in (regular_price, historical_low, target_price)
            if value is not None and value >= 0
        )
        minimum_value = min(range_values)
        maximum_value = max(range_values)
        spread = maximum_value - minimum_value
        padding = max(spread * 0.12, maximum_value * (0.10 if spread == 0 else 0.02), 1.0)
        axis.set_ylim(max(0.0, minimum_value - padding), maximum_value + padding)
        axis.margins(x=0.06)
        handles, legend_labels = axis.get_legend_handles_labels()
        figure.suptitle(
            labels["title"],
            x=0.08,
            y=0.965,
            ha="left",
            color="#F8FAFC",
            fontsize=14,
            fontweight="bold",
        )
        legend = figure.legend(
            handles,
            legend_labels,
            loc="upper center",
            bbox_to_anchor=(0.53, 0.89),
            frameon=False,
            fontsize=8,
            labelcolor="#CBD5E1",
            ncols=PriceChartService._legend_columns(legend_labels),
            columnspacing=1.8,
            handletextpad=0.6,
            borderaxespad=0,
        )
        if legend:
            for line in legend.get_lines():
                line.set_linewidth(2)
        figure.autofmt_xdate(rotation=0)
        figure.subplots_adjust(left=0.12, right=0.96, bottom=0.15, top=0.72)
        output = BytesIO()
        figure.savefig(output, format="png", dpi=160, facecolor=figure.get_facecolor())
        plt.close(figure)
        return output.getvalue()
