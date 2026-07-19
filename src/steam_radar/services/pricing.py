from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from html import escape

from steam_radar.i18n import text


class InvalidPrice(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class NormalizedPrice:
    currency: str
    initial: Decimal
    final: Decimal
    discount_percent: int


def normalize_steam_price(data: dict) -> NormalizedPrice:
    """Convert Steam minor units and validate contradictory discount fields."""
    try:
        currency = str(data["currency"]).upper()
        # Steam Store returns integer minor units for every currency, including JPY/KRW.
        divisor = Decimal(100)
        initial = (Decimal(int(data["initial"])) / divisor).quantize(Decimal("0.01"))
        final = (Decimal(int(data["final"])) / divisor).quantize(Decimal("0.01"))
        reported_discount = int(data.get("discount_percent", 0))
    except (KeyError, TypeError, ValueError, ArithmeticError) as error:
        raise InvalidPrice("Steam returned an incomplete price") from error
    if not currency.isalpha() or len(currency) != 3 or initial < 0 or final < 0:
        raise InvalidPrice("Steam returned an invalid currency or negative price")
    # Steam may keep final == initial during a 100% promotion while final_formatted says "Free".
    # This is observable for real store giveaways, so a reported 100% explicitly means zero.
    if reported_discount == 100 and initial > 0:
        final = Decimal("0.00")
        discount = 100
    elif initial == 0:
        if final != 0:
            raise InvalidPrice("Final price cannot exceed a zero base price")
        discount = 0
    else:
        if final > initial:
            raise InvalidPrice("Current price cannot exceed base price")
        discount = int((((initial - final) / initial) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    # For partial discounts initial/final are authoritative; the reported value is only advisory.
    return NormalizedPrice(currency=currency, initial=initial, final=final, discount_percent=max(0, min(100, discount)))


def format_money(value: Decimal, currency: str) -> str:
    if currency in {"KZT", "RUB", "UAH", "JPY", "KRW"} and value == value.to_integral_value():
        amount = f"{int(value):,}".replace(",", " ")
    else:
        amount = f"{value:,.2f}".replace(",", " ")
    return f"{amount} {currency}"


def format_price_card(name: str, price: NormalizedPrice | None, language: str = "ru") -> str:
    if price is None:
        return f"🎮 <b>{escape(name)}</b>\n\n{text(language, 'price_unavailable')}"
    return (
        f"🎮 <b>{escape(name)}</b>\n\n"
        f"{text(language, 'price_base')}: <b>{format_money(price.initial, price.currency)}</b>\n"
        f"{text(language, 'price_current')}: <b>{format_money(price.final, price.currency)}</b>\n"
        f"{text(language, 'discount')}: <b>{price.discount_percent}%</b>"
    )
