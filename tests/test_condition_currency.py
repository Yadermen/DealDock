from decimal import Decimal
from types import SimpleNamespace

import pytest

from steam_radar.services.condition_currency import convert_user_money_conditions


class ScalarResult:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class Session:
    def __init__(self, rules):
        self.rules = rules

    async def scalars(self, _statement):
        return ScalarResult(self.rules)


class Currency:
    async def rates(self):
        return SimpleNamespace(rates={"PLN": Decimal("4"), "KZT": Decimal("500"), "USD": Decimal("1")})


@pytest.mark.asyncio
async def test_all_monetary_conditions_are_converted_atomically() -> None:
    rule = SimpleNamespace(
        max_price=Decimal("100"),
        minimum_price_drop_amount=Decimal("20"),
        target_currency="PLN",
    )
    changed = await convert_user_money_conditions(Session([rule]), Currency(), 1, "PLN", "KZT")
    assert changed == 1
    assert rule.max_price == Decimal("12500.00")
    assert rule.minimum_price_drop_amount == Decimal("2500.00")
    assert rule.target_currency == "KZT"


def test_monitor_can_compare_converted_target_price() -> None:
    from steam_radar.services.monitor import PriceMonitor

    rule = SimpleNamespace(max_price=Decimal("12500"), min_discount=None, historical_low_only=False)
    assert PriceMonitor._matches(
        rule,
        Decimal("25"),
        0,
        None,
        condition_price=Decimal("12500"),
    )
