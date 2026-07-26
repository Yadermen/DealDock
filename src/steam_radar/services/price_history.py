import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from steam_radar.db.models import PriceHistoryCache, PriceSnapshot
from steam_radar.services.currency import CurrencyError, CurrencyService
from steam_radar.services.itad import IsThereAnyDealProvider, ITADError, ITADHistoryPoint

log = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class PriceHistoryPoint:
    checked_at: datetime
    price: Decimal
    regular_price: Decimal | None
    currency: str


@dataclass(frozen=True, slots=True)
class PriceHistoryResult:
    points: list[PriceHistoryPoint]
    source: str
    stale: bool = False


class PriceHistoryService:
    CACHE_TTL_SECONDS = 6 * 60 * 60

    def __init__(
        self,
        provider: IsThereAnyDealProvider | None,
        session_factory: async_sessionmaker,
        redis: Redis,
        currency_service: CurrencyService | None = None,
    ) -> None:
        self.provider = provider
        self.session_factory = session_factory
        self.redis = redis
        self.currency_service = currency_service

    async def get_history(
        self,
        game_id: int,
        steam_app_id: int,
        country: str,
        currency: str,
        days: int | None,
    ) -> PriceHistoryResult:
        cache_key = f"price-history:v2:{game_id}:{country}:{currency}"
        if cached := await self.redis.get(cache_key):
            result = self._decode(cached, "redis")
            if result and result.points:
                return self._filter(result, days, currency)

        if self.provider is not None:
            try:
                mapping = await self.provider.lookup_steam_ids([steam_app_id])
                external_id = mapping.get(steam_app_id)
                if external_id:
                    rows = await self.provider.steam_history(
                        external_id,
                        country,
                        since=datetime(2000, 1, 1, tzinfo=UTC),
                    )
                    points = await self._external_points(rows, currency)
                    if points:
                        latest_local = await self._latest_local_point(game_id, country, currency)
                        if latest_local is not None:
                            points.append(latest_local)
                        points = self._collapse(points)
                        result = PriceHistoryResult(points, "isthereanydeal")
                        payload = self._encode(result)
                        await self.redis.set(cache_key, payload, ex=self.CACHE_TTL_SECONDS)
                        await self._save_database(game_id, country, currency, result)
                        return self._filter(result, days, currency)
            except ITADError:
                log.warning(
                    "price_history_itad_failed",
                    game_id=game_id,
                    steam_app_id=steam_app_id,
                    country=country,
                    exc_info=True,
                )

        database = await self._database_history(game_id, country)
        if database and database.points:
            filtered_database = self._filter(
                PriceHistoryResult(database.points, database.source, stale=True),
                days,
                currency,
            )
            if filtered_database.points:
                return filtered_database

        local = await self._local_history(game_id, country, currency)
        return self._filter(PriceHistoryResult(local, "dealdock"), days, currency)

    async def _save_database(
        self,
        game_id: int,
        country: str,
        currency: str,
        result: PriceHistoryResult,
    ) -> None:
        payload = json.loads(self._encode(result))["points"]
        async with self.session_factory() as session:
            await session.execute(
                pg_insert(PriceHistoryCache)
                .values(
                    game_id=game_id,
                    country_code=country,
                    currency=currency,
                    source=result.source,
                    payload=payload,
                    updated_at=datetime.now(UTC),
                )
                .on_conflict_do_update(
                    index_elements=[PriceHistoryCache.game_id, PriceHistoryCache.country_code],
                    set_={
                        "currency": currency,
                        "source": result.source,
                        "payload": payload,
                        "updated_at": datetime.now(UTC),
                    },
                )
            )
            await session.commit()

    async def _database_history(self, game_id: int, country: str) -> PriceHistoryResult | None:
        async with self.session_factory() as session:
            cached = await session.scalar(
                select(PriceHistoryCache).where(
                    PriceHistoryCache.game_id == game_id,
                    PriceHistoryCache.country_code == country,
                )
            )
        if cached is None:
            return None
        return self._decode(
            json.dumps({"points": cached.payload}, ensure_ascii=False),
            cached.source,
        )

    async def _local_history(self, game_id: int, country: str, currency: str) -> list[PriceHistoryPoint]:
        async with self.session_factory() as session:
            snapshots = list(
                (
                    await session.scalars(
                        select(PriceSnapshot)
                        .where(
                            PriceSnapshot.game_id == game_id,
                            PriceSnapshot.country_code == country,
                            PriceSnapshot.currency == currency,
                        )
                        .order_by(PriceSnapshot.checked_at)
                    )
                ).all()
            )
        return self._collapse(
            [
                PriceHistoryPoint(item.checked_at, item.final_price, item.initial_price, item.currency)
                for item in snapshots
            ]
        )

    async def _latest_local_point(self, game_id: int, country: str, currency: str) -> PriceHistoryPoint | None:
        async with self.session_factory() as session:
            snapshot = await session.scalar(
                select(PriceSnapshot)
                .where(
                    PriceSnapshot.game_id == game_id,
                    PriceSnapshot.country_code == country,
                    PriceSnapshot.currency == currency,
                )
                .order_by(PriceSnapshot.checked_at.desc())
                .limit(1)
            )
        return (
            PriceHistoryPoint(snapshot.checked_at, snapshot.final_price, snapshot.initial_price, snapshot.currency)
            if snapshot is not None
            else None
        )

    async def _external_points(self, rows: list[ITADHistoryPoint], target_currency: str) -> list[PriceHistoryPoint]:
        points: list[PriceHistoryPoint] = []
        rates = None
        for row in rows:
            price = row.price
            regular = row.regular_price
            if row.currency != target_currency:
                if self.currency_service is None:
                    continue
                try:
                    rates = rates or await self.currency_service.rates()
                    price = (price / rates.rates[row.currency] * rates.rates[target_currency]).quantize(Decimal("0.01"))
                    regular = (
                        (regular / rates.rates[row.currency] * rates.rates[target_currency]).quantize(Decimal("0.01"))
                        if regular is not None
                        else None
                    )
                except (CurrencyError, KeyError, ZeroDivisionError):
                    log.warning(
                        "price_history_currency_conversion_failed",
                        source_currency=row.currency,
                        target_currency=target_currency,
                        exc_info=True,
                    )
                    continue
            points.append(PriceHistoryPoint(row.checked_at, price, regular, target_currency))
        return points

    @staticmethod
    def _collapse(points: list[PriceHistoryPoint]) -> list[PriceHistoryPoint]:
        collapsed: list[PriceHistoryPoint] = []
        for point in sorted(points, key=lambda item: item.checked_at):
            if collapsed and collapsed[-1].price == point.price:
                continue
            collapsed.append(point)
        return collapsed

    @staticmethod
    def _filter(result: PriceHistoryResult, days: int | None, currency: str) -> PriceHistoryResult:
        cutoff = datetime.now(UTC) - timedelta(days=days) if days else None
        currency_points = [point for point in result.points if point.currency == currency]
        points = [point for point in currency_points if cutoff is None or point.checked_at >= cutoff]
        if cutoff is not None:
            predecessor = next(
                (point for point in reversed(currency_points) if point.checked_at < cutoff),
                None,
            )
            if predecessor is not None:
                points.insert(0, PriceHistoryPoint(cutoff, predecessor.price, predecessor.regular_price, currency))
        return PriceHistoryResult(points, result.source, result.stale)

    @staticmethod
    def _encode(result: PriceHistoryResult) -> str:
        return json.dumps(
            {
                "source": result.source,
                "points": [
                    {
                        **asdict(point),
                        "checked_at": point.checked_at.isoformat(),
                        "price": str(point.price),
                        "regular_price": str(point.regular_price) if point.regular_price is not None else None,
                    }
                    for point in result.points
                ],
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _decode(payload: str, source: str) -> PriceHistoryResult | None:
        try:
            data = json.loads(payload)
            points = [
                PriceHistoryPoint(
                    datetime.fromisoformat(item["checked_at"]).astimezone(UTC),
                    Decimal(item["price"]),
                    Decimal(item["regular_price"]) if item.get("regular_price") is not None else None,
                    item["currency"],
                )
                for item in data["points"]
            ]
            return PriceHistoryResult(points, data.get("source") or source)
        except (KeyError, TypeError, ValueError):
            return None
