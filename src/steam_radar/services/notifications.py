from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from html import escape

from steam_radar.i18n import text
from steam_radar.services.premium_analytics import price_analytics
from steam_radar.services.pricing import format_money

CAPTION_LIMIT = 1024


@dataclass(frozen=True, slots=True)
class NotificationData:
    app_id: int
    name: str
    image_url: str | None
    region_name: str
    currency: str
    base_price: Decimal | None
    current_price: Decimal
    discount_percent: int
    previous_price: Decimal | None
    observed_prices: list[Decimal]
    reasons: list[str]
    updated_at: datetime
    premium: bool
    steam_historical_low: Decimal | None = None


def build_game_notification(data: NotificationData, language: str) -> str:
    url = f"https://store.steampowered.com/app/{data.app_id}"
    lines = [
        text(language, "notification_price_dropped"),
        f'\n🎮 <a href="{url}"><b>{escape(data.name)}</b></a>',
        f"\n💰 <b>{text(language, 'notification_price')}</b>",
    ]
    if data.base_price is not None and data.base_price >= data.current_price:
        lines.append(
            f"<s>{format_money(data.base_price, data.currency)}</s> → "
            f"<b>{format_money(data.current_price, data.currency)}</b>"
        )
        saving = data.base_price - data.current_price
        if saving > 0:
            lines.append(f"💵 {text(language, 'notification_saving')}: <b>{format_money(saving, data.currency)}</b>")
    else:
        lines.append(f"<b>{format_money(data.current_price, data.currency)}</b>")
    if data.discount_percent > 0:
        lines.append(f"🏷 <b>{text(language, 'discount')}</b>: −{data.discount_percent}%")
    lines.append(f"🌍 <b>{text(language, 'region_label')}</b>: {escape(data.region_name)}")
    reason_keys = {
        "target": "reason_target",
        "discount": "reason_discount",
        "new_low": "reason_new_low",
        "known_low": "reason_known_low",
        "any_drop": "reason_any_drop",
        "drop_amount": "reason_drop_amount",
        "drop_percent": "reason_drop_percent",
        "rule": "reason_rule",
    }
    if data.reasons:
        lines.append(f"\n✅ <b>{text(language, 'notification_why')}</b>")
        lines.extend(f"• {text(language, reason_keys[reason])}" for reason in data.reasons if reason in reason_keys)
    if data.premium:
        analytics = price_analytics(data.observed_prices, data.base_price)
        lines.append(f"\n⭐ <b>{text(language, 'notification_premium_analysis')}</b>")
        if analytics is None or analytics.score is None:
            lines.append(text(language, "notification_analysis_insufficient"))
        else:
            lines.extend(
                [
                    f"📉 {text(language, 'notification_observed_low')}: "
                    f"<b>{format_money(analytics.minimum, data.currency)}</b>",
                    f"📈 {text(language, 'price_max')}: <b>{format_money(analytics.maximum, data.currency)}</b>",
                    f"📊 {text(language, 'price_average')}: <b>{format_money(analytics.average, data.currency)}</b>",
                    f"⭐ {text(language, 'notification_deal_score')}: <b>{analytics.score}/100</b>",
                    f"{text(language, analytics.direction_key)}",
                    f"🔢 {text(language, 'price_observations')}: {analytics.sample_count}",
                ]
            )
        if data.steam_historical_low is not None:
            lines.append(
                f"🏆 {text(language, 'notification_steam_historical_low')}: "
                f"<b>{format_money(data.steam_historical_low, data.currency)}</b>"
            )
    lines.append(f"\n🕐 {text(language, 'notification_updated')}: {data.updated_at:%d.%m.%Y, %H:%M}")
    caption = "\n".join(lines)
    return caption if len(caption) <= CAPTION_LIMIT else caption[: CAPTION_LIMIT - 1].rsplit("\n", 1)[0]


def format_optional_price(value: Decimal | None, currency: str) -> str | None:
    return format_money(value, currency) if value is not None else None


def format_price_difference(current: Decimal, reference: Decimal, currency: str) -> str:
    return format_money(abs(current - reference), currency)
