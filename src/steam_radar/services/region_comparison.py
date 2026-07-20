import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import structlog

from steam_radar.constants import REGIONS
from steam_radar.services.currency import ExchangeRates
from steam_radar.services.steam import SteamError, SteamPrice, SteamProvider

log = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class CachedRegionalPrice:
    price: SteamPrice
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class RegionalResult:
    region: str
    price: SteamPrice
    converted: Decimal | None
    source: str
    checked_at: datetime
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    prices: list[RegionalResult]
    unavailable: list[str]
    unconverted: list[str]


class RegionalPriceComparison:
    """Fetch missing regional prices concurrently while preserving partial results."""

    def __init__(self, steam: SteamProvider, concurrency: int = 4, timeout: float = 9.0) -> None:
        self.steam = steam
        self.concurrency = concurrency
        self.timeout = timeout

    async def compare(
        self,
        app_id: int,
        regions: list[str],
        target_currency: str,
        rates: ExchangeRates,
        cached: dict[str, CachedRegionalPrice],
        language: str,
        force: bool = False,
    ) -> ComparisonResult:
        fetched = await self.fetch(app_id, regions, cached, language, force)
        return self.apply_rates(fetched, target_currency, rates, app_id)

    async def fetch(
        self,
        app_id: int,
        regions: list[str],
        cached: dict[str, CachedRegionalPrice],
        language: str,
        force: bool = False,
    ) -> ComparisonResult:
        semaphore = asyncio.Semaphore(self.concurrency)

        async def load(code: str) -> tuple[str, SteamPrice | None, str, datetime, Exception | None]:
            saved = cached.get(code)
            if saved is not None and not force:
                return code, saved.price, "database", saved.checked_at, None
            error: Exception | None = None
            for attempt in range(2):
                try:
                    async with semaphore:
                        _, price = await asyncio.wait_for(
                            self.steam.details(
                                app_id,
                                REGIONS[code].steam_country_code,
                                language,
                                force_refresh=force,
                            ),
                            timeout=self.timeout,
                        )
                    return code, price, "steam", datetime.now(UTC), None
                except Exception as caught:
                    error = caught
                    retryable = isinstance(caught, TimeoutError) or (
                        isinstance(caught, SteamError) and caught.transient
                    )
                    if not retryable or attempt == 1:
                        break
                    await asyncio.sleep(0.2)
            if error is not None:
                log.warning(
                    "regional_price_failed",
                    steam_app_id=app_id,
                    region=code,
                    country_code=REGIONS[code].steam_country_code,
                    source="steam",
                    cache_used=False,
                    exception_type=type(error).__name__,
                    http_status=getattr(error, "status_code", None),
                    timeout=isinstance(error, TimeoutError),
                    exc_info=True,
                )
                return code, None, "steam", datetime.now(UTC), error
            return code, None, "steam", datetime.now(UTC), RuntimeError("regional price unavailable")

        loaded = await asyncio.gather(*(load(code) for code in regions), return_exceptions=True)
        prices: list[RegionalResult] = []
        unavailable: list[str] = []
        unconverted: list[str] = []
        for index, item in enumerate(loaded):
            if isinstance(item, BaseException):
                code = regions[index]
                log.exception("regional_price_unexpected", steam_app_id=app_id, region=code, exc_info=item)
                unavailable.append(code)
                continue
            code, price, source, checked_at, error = item
            if price is None:
                unavailable.append(code)
                continue
            prices.append(RegionalResult(code, price, None, source, checked_at, str(error) if error else None))
        return ComparisonResult(prices, unavailable, unconverted)

    def apply_rates(
        self, fetched: ComparisonResult, target_currency: str, rates: ExchangeRates, app_id: int
    ) -> ComparisonResult:
        prices: list[RegionalResult] = []
        unconverted: list[str] = []
        for item in fetched.prices:
            converted = self._convert(item.price.final, item.price.currency, target_currency, rates)
            if converted is None:
                unconverted.append(item.region)
            prices.append(RegionalResult(item.region, item.price, converted, item.source, item.checked_at, item.error))
        log.info(
            "regional_comparison_completed",
            steam_app_id=app_id,
            successful=len(prices),
            unavailable=len(fetched.unavailable),
            unconverted=len(unconverted),
            currencies=sorted({item.price.currency for item in prices}),
            rate_source=rates.source,
            rates_stale=rates.stale,
        )
        return ComparisonResult(prices, fetched.unavailable, unconverted)

    @staticmethod
    def _convert(amount: Decimal, source: str, target: str, rates: ExchangeRates) -> Decimal | None:
        if source == target:
            return amount.quantize(Decimal("0.01"))
        try:
            return (amount / rates.rates[source] * rates.rates[target]).quantize(Decimal("0.01"))
        except (KeyError, InvalidOperation, ZeroDivisionError):
            return None
