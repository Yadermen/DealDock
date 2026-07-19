from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from steam_radar.services.currency import CurrencyError, CurrencyService


class MemoryRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, **kwargs):
        self.data[key] = value


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class Client:
    def __init__(self, payload=None, error=None):
        self.payload, self.error = payload, error
        self.calls = 0

    async def get(self, url):
        self.calls += 1
        if self.error:
            raise self.error
        return Response(self.payload)


@pytest.mark.asyncio
async def test_decimal_conversion_and_cache_reuse() -> None:
    payload = {
        "result": "success",
        "time_last_update_unix": int(datetime.now(UTC).timestamp()),
        "rates": {"USD": 1, "EUR": 0.8, "PLN": 4},
    }
    client = Client(payload)
    service = CurrencyService(MemoryRedis(), client)
    value, _ = await service.convert(Decimal("10"), "EUR", "PLN")
    assert value == Decimal("50.00")
    await service.convert(Decimal("1"), "USD", "EUR")
    assert client.calls == 1


@pytest.mark.asyncio
async def test_missing_rate_is_not_invented() -> None:
    payload = {
        "result": "success",
        "time_last_update_unix": int(datetime.now(UTC).timestamp()),
        "rates": {"USD": 1},
    }
    service = CurrencyService(MemoryRedis(), Client(payload))
    with pytest.raises(CurrencyError):
        await service.convert(Decimal("10"), "XXX", "USD")


@pytest.mark.asyncio
async def test_recent_cached_rates_survive_provider_failure() -> None:
    redis = MemoryRedis()
    good = CurrencyService(
        redis,
        Client(
            {
                "result": "success",
                "time_last_update_unix": int(datetime.now(UTC).timestamp()),
                "rates": {"USD": 1, "EUR": 0.9},
            }
        ),
        ttl=0,
    )
    await good.rates()
    failing = CurrencyService(redis, Client(error=httpx.ConnectError("offline")), ttl=0)
    rates = await failing.rates()
    assert rates.stale
    assert rates.rates["EUR"] == Decimal("0.9")
