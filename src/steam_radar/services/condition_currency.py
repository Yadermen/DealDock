from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from steam_radar.db.models import WatchRule
from steam_radar.services.currency import CurrencyError, CurrencyService


async def convert_user_money_conditions(
    session: AsyncSession,
    currency_service: CurrencyService,
    user_id: int,
    source_default: str,
    target_currency: str,
) -> int:
    """Convert every persisted monetary notification condition in one transaction."""
    rules = list((await session.scalars(select(WatchRule).where(WatchRule.user_id == user_id))).all())
    rates = None
    changed = 0
    for rule in rules:
        amounts = ("max_price", "minimum_price_drop_amount")
        if not any(getattr(rule, field) is not None for field in amounts):
            continue
        source_currency = rule.target_currency or source_default
        if source_currency == target_currency:
            rule.target_currency = target_currency
            continue
        rates = rates or await currency_service.rates()
        try:
            factor = rates.rates[target_currency] / rates.rates[source_currency]
        except (KeyError, ZeroDivisionError) as error:
            raise CurrencyError(f"missing exchange rate for {source_currency} or {target_currency}") from error
        for field in amounts:
            value: Decimal | None = getattr(rule, field)
            if value is not None:
                setattr(rule, field, (value * factor).quantize(Decimal("0.01")))
        rule.target_currency = target_currency
        changed += 1
    return changed
