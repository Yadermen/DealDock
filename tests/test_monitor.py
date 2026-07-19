from decimal import Decimal
from types import SimpleNamespace

from steam_radar.services.monitor import PriceMonitor


def test_rule_matches_discount() -> None:
    rule = SimpleNamespace(min_discount=50, max_price=None, historical_low_only=False)
    assert PriceMonitor._matches(rule, Decimal("10"), 50, Decimal("9"))
    assert not PriceMonitor._matches(rule, Decimal("10"), 49, Decimal("9"))


def test_rule_matches_max_price() -> None:
    rule = SimpleNamespace(min_discount=None, max_price=Decimal("500"), historical_low_only=False)
    assert PriceMonitor._matches(rule, Decimal("499"), 0, None)


def test_historical_low_filter() -> None:
    rule = SimpleNamespace(min_discount=1, max_price=None, historical_low_only=True)
    assert not PriceMonitor._matches(rule, Decimal("500"), 50, Decimal("400"))


class BusyLock:
    async def acquire(self):
        return False


class BusyRedis:
    def lock(self, *args, **kwargs):
        return BusyLock()


async def test_price_monitor_does_not_duplicate_running_job() -> None:
    monitor = PriceMonitor(
        bot=SimpleNamespace(),
        session_factory=None,
        steam=SimpleNamespace(redis=BusyRedis()),
        settings=SimpleNamespace(),
    )
    assert await monitor.run() == 0


def test_snapshot_is_saved_when_base_price_or_currency_changes() -> None:
    latest = SimpleNamespace(
        initial_price=Decimal("100"),
        final_price=Decimal("50"),
        discount_percent=50,
        currency="EUR",
    )
    same = SimpleNamespace(initial=Decimal("100"), final=Decimal("50"), discount_percent=50, currency="EUR")
    new_base = SimpleNamespace(initial=Decimal("120"), final=Decimal("50"), discount_percent=50, currency="EUR")
    new_currency = SimpleNamespace(initial=Decimal("100"), final=Decimal("50"), discount_percent=50, currency="PLN")
    assert not PriceMonitor._price_changed(latest, same)
    assert PriceMonitor._price_changed(latest, new_base)
    assert PriceMonitor._price_changed(latest, new_currency)
