import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from steam_radar.services.currency import ExchangeRates
from steam_radar.services.region_comparison import CachedRegionalPrice, RegionalPriceComparison
from steam_radar.services.steam import SteamError, SteamPrice


class FakeSteam:
    def __init__(self, failing: set[str] | None = None) -> None:
        self.active = 0
        self.maximum_active = 0
        self.calls: list[str] = []
        self.failing = failing or set()

    async def details(self, app_id, country, language, force_refresh=False):
        self.calls.append(country)
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        if country in self.failing:
            raise TimeoutError(country)
        return None, SteamPrice(app_id, "EUR", Decimal("20"), Decimal("10"), 50)


def rates(**values: str) -> ExchangeRates:
    return ExchangeRates({key: Decimal(value) for key, value in values.items()}, datetime.now(UTC))


@pytest.mark.asyncio
async def test_regions_are_parallel_with_bounded_concurrency() -> None:
    steam = FakeSteam()
    service = RegionalPriceComparison(steam, concurrency=2)
    result = await service.compare(10, ["DE", "FR", "IT", "ES"], "USD", rates(EUR="1", USD="1"), {}, "english")
    assert len(result.prices) == 4
    assert steam.maximum_active == 2


@pytest.mark.asyncio
async def test_fresh_cache_avoids_steam_and_force_bypasses_it() -> None:
    steam = FakeSteam()
    price = SteamPrice(10, "EUR", Decimal("20"), Decimal("10"), 50)
    cached = {"DE": CachedRegionalPrice(price, datetime.now(UTC))}
    service = RegionalPriceComparison(steam)
    result = await service.compare(10, ["DE"], "USD", rates(EUR="1", USD="1"), cached, "english")
    assert result.prices[0].source == "database"
    assert not steam.calls
    await service.compare(10, ["DE"], "USD", rates(EUR="1", USD="1"), cached, "english", force=True)
    assert steam.calls == ["DE"]


@pytest.mark.asyncio
async def test_failed_region_and_missing_rate_only_remove_affected_conversion() -> None:
    steam = FakeSteam(failing={"DE"})
    service = RegionalPriceComparison(steam)
    result = await service.compare(10, ["DE", "PL"], "USD", rates(USD="1"), {}, "english")
    assert result.unavailable == ["DE"]
    assert result.unconverted == ["PL"]
    assert result.prices[0].price.currency == "EUR"


@pytest.mark.asyncio
async def test_target_currency_does_not_need_exchange_rate() -> None:
    steam = FakeSteam()
    result = await RegionalPriceComparison(steam).compare(10, ["DE"], "EUR", rates(EUR="1"), {}, "english")
    assert result.prices[0].converted == Decimal("10.00")
    assert not result.unconverted


@pytest.mark.asyncio
async def test_region_timeout_does_not_hang_whole_result() -> None:
    class HangingSteam(FakeSteam):
        async def details(self, *args, **kwargs):
            await asyncio.sleep(1)

    result = await RegionalPriceComparison(HangingSteam(), timeout=0.01).compare(
        10, ["DE"], "USD", rates(EUR="1", USD="1"), {}, "english"
    )
    assert result.unavailable == ["DE"]


@pytest.mark.asyncio
async def test_permanent_404_is_not_retried() -> None:
    class MissingSteam(FakeSteam):
        async def details(self, *args, **kwargs):
            self.calls.append("missing")
            raise SteamError("missing", status_code=404, transient=False)

    steam = MissingSteam()
    result = await RegionalPriceComparison(steam).compare(10, ["DE"], "USD", rates(EUR="1", USD="1"), {}, "english")
    assert result.unavailable == ["DE"]
    assert steam.calls == ["missing"]
