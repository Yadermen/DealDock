import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx
import structlog
from redis.asyncio import Redis
from redis.exceptions import LockError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from steam_radar.db.models import CurrencyRateCache

log = structlog.get_logger()


class CurrencyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExchangeRates:
    rates: dict[str, Decimal]
    updated_at: datetime
    stale: bool = False
    source: str = "unknown"


class CurrencyService:
    """USD-based rates with Redis, provider and persistent PostgreSQL fallback."""

    URL = "https://open.er-api.com/v6/latest/USD"
    CACHE_KEY = "currency:rates:usd"
    LOCK_KEY = "lock:currency:rates:usd"

    def __init__(
        self,
        redis: Redis,
        client: httpx.AsyncClient,
        ttl: int = 21_600,
        stale_hours: int = 72,
        session_factory: async_sessionmaker | None = None,
    ) -> None:
        self.redis, self.client = redis, client
        self.ttl = ttl
        self.stale_limit = timedelta(hours=stale_hours)
        self.session_factory = session_factory

    async def rates(self, force: bool = False) -> ExchangeRates:
        cached = await self._cached()
        if cached and not force and datetime.now(UTC) - cached.updated_at <= timedelta(seconds=self.ttl):
            return ExchangeRates(cached.rates, cached.updated_at, source="redis")
        lock = self.redis.lock(self.LOCK_KEY, timeout=30, blocking_timeout=1)
        if not await lock.acquire():
            await asyncio.sleep(0.2)
            concurrent = await self._cached()
            if concurrent:
                return ExchangeRates(concurrent.rates, concurrent.updated_at, concurrent.stale, "redis")
            return await self._database_fallback()
        try:
            if not force:
                refreshed = await self._cached()
                if refreshed and datetime.now(UTC) - refreshed.updated_at <= timedelta(seconds=self.ttl):
                    return ExchangeRates(refreshed.rates, refreshed.updated_at, source="redis")
            try:
                response = await self.client.get(self.URL, timeout=8)
                response.raise_for_status()
                payload = response.json()
                if payload.get("result") != "success" or not isinstance(payload.get("rates"), dict):
                    raise CurrencyError("currency provider returned an invalid response")
                rates = {
                    code: Decimal(str(value)) for code, value in payload["rates"].items() if Decimal(str(value)) > 0
                }
                rates["USD"] = Decimal(1)
                updated = datetime.fromtimestamp(
                    payload.get("time_last_update_unix", datetime.now(UTC).timestamp()), UTC
                )
                result = ExchangeRates(rates, updated, source="open-er-api")
                await self._save(result)
                log.info("currency_rates_updated", source=result.source, currencies=len(rates))
                return result
            except (httpx.HTTPError, ValueError, InvalidOperation, CurrencyError) as error:
                log.warning(
                    "currency_provider_failed",
                    source="open-er-api",
                    exception_type=type(error).__name__,
                    http_status=getattr(getattr(error, "response", None), "status_code", None),
                )
                if cached and datetime.now(UTC) - cached.updated_at <= self.stale_limit:
                    log.warning("currency_rates_stale_fallback", source="redis")
                    return ExchangeRates(cached.rates, cached.updated_at, True, "redis-stale")
                return await self._database_fallback(error)
        finally:
            try:
                await lock.release()
            except LockError:
                pass

    async def convert(self, amount: Decimal, source: str, target: str) -> tuple[Decimal, ExchangeRates]:
        if source == target:
            return amount.quantize(Decimal("0.01")), ExchangeRates(
                {source: Decimal(1)}, datetime.now(UTC), source="identity"
            )
        data = await self.rates()
        try:
            converted = amount / data.rates[source] * data.rates[target]
        except (KeyError, InvalidOperation, ZeroDivisionError) as error:
            raise CurrencyError(f"missing exchange rate for {source} or {target}") from error
        return converted.quantize(Decimal("0.01")), data

    async def _save(self, result: ExchangeRates) -> None:
        payload = {"updated_at": result.updated_at.isoformat(), "rates": {k: str(v) for k, v in result.rates.items()}}
        await self.redis.set(self.CACHE_KEY, json.dumps(payload), ex=int(self.stale_limit.total_seconds()))
        if not self.session_factory:
            return
        async with self.session_factory() as session:
            statement = pg_insert(CurrencyRateCache).values(
                id=1,
                base_currency="USD",
                rates=payload["rates"],
                provider_updated_at=result.updated_at,
                saved_at=datetime.now(UTC),
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "rates": payload["rates"],
                        "provider_updated_at": result.updated_at,
                        "saved_at": datetime.now(UTC),
                    },
                )
            )
            await session.commit()

    async def _database_fallback(self, cause: Exception | None = None) -> ExchangeRates:
        if self.session_factory:
            async with self.session_factory() as session:
                saved = await session.scalar(select(CurrencyRateCache).where(CurrencyRateCache.id == 1))
                if saved:
                    rates = {code: Decimal(value) for code, value in saved.rates.items()}
                    log.warning("currency_rates_stale_fallback", source="postgresql")
                    return ExchangeRates(rates, saved.provider_updated_at, True, "postgresql-stale")
        raise CurrencyError("exchange rates are temporarily unavailable") from cause

    async def _cached(self) -> ExchangeRates | None:
        raw = await self.redis.get(self.CACHE_KEY)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            return ExchangeRates(
                rates={code: Decimal(value) for code, value in payload["rates"].items()},
                updated_at=datetime.fromisoformat(payload["updated_at"]).astimezone(UTC),
                source="redis",
            )
        except (ValueError, KeyError, TypeError, InvalidOperation):
            return None
