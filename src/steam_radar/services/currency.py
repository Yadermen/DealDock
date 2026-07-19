import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx
from redis.asyncio import Redis


class CurrencyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExchangeRates:
    rates: dict[str, Decimal]
    updated_at: datetime
    stale: bool = False


class CurrencyService:
    """Cached USD-based fiat rates from ExchangeRate-API's open endpoint."""

    URL = "https://open.er-api.com/v6/latest/USD"
    CACHE_KEY = "currency:rates:usd"

    def __init__(self, redis: Redis, client: httpx.AsyncClient, ttl: int = 21_600, stale_hours: int = 72) -> None:
        self.redis, self.client = redis, client
        self.ttl = ttl
        self.stale_limit = timedelta(hours=stale_hours)

    async def rates(self, force: bool = False) -> ExchangeRates:
        cached = await self._cached()
        if cached and not force and datetime.now(UTC) - cached.updated_at <= timedelta(seconds=self.ttl):
            return cached
        try:
            response = await self.client.get(self.URL)
            response.raise_for_status()
            payload = response.json()
            if payload.get("result") != "success" or not isinstance(payload.get("rates"), dict):
                raise CurrencyError("currency provider returned an invalid response")
            rates = {code: Decimal(str(value)) for code, value in payload["rates"].items() if Decimal(str(value)) > 0}
            rates["USD"] = Decimal(1)
            updated = datetime.fromtimestamp(payload.get("time_last_update_unix", datetime.now(UTC).timestamp()), UTC)
            result = ExchangeRates(rates=rates, updated_at=updated)
            await self.redis.set(
                self.CACHE_KEY,
                json.dumps({"updated_at": updated.isoformat(), "rates": {k: str(v) for k, v in rates.items()}}),
            )
            return result
        except (httpx.HTTPError, ValueError, InvalidOperation, CurrencyError) as error:
            if cached and datetime.now(UTC) - cached.updated_at <= self.stale_limit:
                return ExchangeRates(cached.rates, cached.updated_at, stale=True)
            raise CurrencyError("exchange rates are temporarily unavailable") from error

    async def convert(self, amount: Decimal, source: str, target: str) -> tuple[Decimal, ExchangeRates]:
        data = await self.rates()
        try:
            converted = amount / data.rates[source] * data.rates[target]
        except (KeyError, InvalidOperation, ZeroDivisionError) as error:
            raise CurrencyError(f"missing exchange rate for {source} or {target}") from error
        return converted.quantize(Decimal("0.01")), data

    async def _cached(self) -> ExchangeRates | None:
        raw = await self.redis.get(self.CACHE_KEY)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            return ExchangeRates(
                rates={code: Decimal(value) for code, value in payload["rates"].items()},
                updated_at=datetime.fromisoformat(payload["updated_at"]).astimezone(UTC),
            )
        except (ValueError, KeyError, TypeError, InvalidOperation):
            return None
