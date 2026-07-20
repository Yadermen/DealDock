from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

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

    def lock(self, *args, **kwargs):
        return MemoryLock()


class MemoryLock:
    async def acquire(self):
        return True

    async def release(self):
        return None


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

    async def get(self, url, **kwargs):
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


@pytest.mark.asyncio
async def test_database_rates_are_last_resort_when_provider_and_redis_fail() -> None:
    saved = SimpleNamespace(
        rates={"USD": "1", "PLN": "4.1"},
        provider_updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    class Session:
        async def scalar(self, statement):
            return saved

    class Context:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *args):
            return None

    service = CurrencyService(
        MemoryRedis(),
        Client(error=httpx.ConnectError("offline")),
        session_factory=lambda: Context(),
    )
    rates = await service.rates()
    assert rates.source == "postgresql-stale"
    assert rates.stale and rates.rates["PLN"] == Decimal("4.1")


@pytest.mark.asyncio
async def test_identity_conversion_needs_no_provider_call() -> None:
    client = Client(error=AssertionError("provider must not be called"))
    value, rates = await CurrencyService(MemoryRedis(), client).convert(Decimal("12.34"), "PLN", "PLN")
    assert value == Decimal("12.34") and rates.source == "identity"
    assert client.calls == 0
