import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx
import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from steam_radar.db.models import ExternalHistoricalLow, Plan, User, WatchRule

log = structlog.get_logger()


class ITADError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HistoricalLow:
    game_id: str
    scope: str
    shop_name: str | None
    price: Decimal
    currency: str
    occurred_at: datetime | None


@dataclass(frozen=True, slots=True)
class ITADHistoryPoint:
    checked_at: datetime
    price: Decimal
    regular_price: Decimal | None
    currency: str


class IsThereAnyDealProvider:
    BASE_URL = "https://api.isthereanydeal.com"
    STEAM_SHOP_ID = 61

    def __init__(self, client: httpx.AsyncClient, api_key: str) -> None:
        self.client = client
        self.api_key = api_key

    async def search_titles(self, query: str, limit: int = 8) -> list[str]:
        """Resolve aliases to canonical titles; Steam remains the authority for App IDs."""
        rows = await self._get("/games/search/v1", title=query, results=limit)
        return [
            str(row["title"]).strip()
            for row in rows
            if isinstance(row, dict) and str(row.get("title", "")).strip()
        ]

    async def lookup_steam_ids(self, app_ids: list[int]) -> dict[int, str]:
        if not app_ids:
            return {}
        payload = [f"app/{app_id}" for app_id in app_ids]
        response = await self._post(f"/lookup/id/shop/{self.STEAM_SHOP_ID}/v1", payload)
        return {
            int(shop_id.split("/", 1)[1]): game_id
            for shop_id, game_id in response.items()
            if shop_id.startswith("app/") and game_id
        }

    async def historical_lows(self, game_ids: list[str], country: str) -> list[HistoricalLow]:
        if not game_ids:
            return []
        all_rows = await self._post("/games/historylow/v1", game_ids, country=country)
        store_rows = await self._post("/games/storelow/v2", game_ids, country=country)
        result: list[HistoricalLow] = []
        for row in all_rows:
            parsed = self._parse_low(row.get("id"), "all_stores", row.get("low"))
            if parsed:
                result.append(parsed)
        for row in store_rows:
            steam = next(
                (
                    low
                    for low in row.get("lows", [])
                    if low.get("shop", {}).get("id") == self.STEAM_SHOP_ID
                    or str(low.get("shop", {}).get("name", "")).casefold() == "steam"
                ),
                None,
            )
            parsed = self._parse_low(row.get("id"), "steam", steam)
            if parsed:
                result.append(parsed)
        return result

    async def steam_history(
        self,
        game_id: str,
        country: str,
        since: datetime | None = None,
    ) -> list[ITADHistoryPoint]:
        params: dict[str, object] = {
            "id": game_id,
            "country": country,
            "shops": self.STEAM_SHOP_ID,
        }
        if since is not None:
            params["since"] = since.isoformat()
        rows = await self._get("/games/history/v2", **params)
        points: list[ITADHistoryPoint] = []
        for row in rows:
            shop = row.get("shop") or {}
            if shop.get("id") != self.STEAM_SHOP_ID and str(shop.get("name", "")).casefold() != "steam":
                continue
            deal = row.get("deal") or {}
            price = deal.get("price") or {}
            regular = deal.get("regular") or {}
            try:
                checked_at = datetime.fromisoformat(row["timestamp"]).astimezone(UTC)
                amount = Decimal(str(price["amount"]))
                currency = str(price["currency"]).upper()
                regular_amount = Decimal(str(regular["amount"])) if regular.get("amount") is not None else None
            except (KeyError, TypeError, ValueError, InvalidOperation):
                continue
            if amount < 0 or len(currency) != 3:
                continue
            points.append(ITADHistoryPoint(checked_at, amount, regular_amount, currency))
        return sorted(points, key=lambda item: item.checked_at)

    async def _post(self, path: str, payload: list[str], **params):
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self.client.post(
                    self.BASE_URL + path,
                    params=params,
                    json=payload,
                    headers={"ITAD-API-Key": self.api_key},
                    timeout=10,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as error:
                last_error = error
                if error.response.status_code not in {429, 500, 502, 503, 504} or attempt == 1:
                    break
            except (httpx.TimeoutException, httpx.RequestError) as error:
                last_error = error
                if attempt == 1:
                    break
            except ValueError as error:
                raise ITADError(f"ITAD returned invalid JSON at {path}") from error
            await asyncio.sleep(0.4 * (attempt + 1))
        raise ITADError(f"ITAD request failed at {path}") from last_error

    async def _get(self, path: str, **params):
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self.client.get(
                    self.BASE_URL + path,
                    params=params,
                    headers={"ITAD-API-Key": self.api_key},
                    timeout=10,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as error:
                last_error = error
                if error.response.status_code not in {429, 500, 502, 503, 504} or attempt == 1:
                    break
            except (httpx.TimeoutException, httpx.RequestError) as error:
                last_error = error
                if attempt == 1:
                    break
            except ValueError as error:
                raise ITADError(f"ITAD returned invalid JSON at {path}") from error
            await asyncio.sleep(0.4 * (attempt + 1))
        raise ITADError(f"ITAD request failed at {path}") from last_error

    @staticmethod
    def _parse_low(game_id: str | None, scope: str, low: dict | None) -> HistoricalLow | None:
        if not game_id or not low:
            return None
        price = low.get("price") or low.get("deal", {}).get("price")
        try:
            amount = Decimal(str(price["amount"]))
            currency = str(price["currency"]).upper()
            if amount < 0 or len(currency) != 3:
                return None
        except (KeyError, TypeError, InvalidOperation):
            return None
        timestamp = low.get("timestamp")
        try:
            occurred_at = datetime.fromisoformat(timestamp).astimezone(UTC) if timestamp else None
        except ValueError:
            occurred_at = None
        shop = low.get("shop") or {}
        return HistoricalLow(game_id, scope, shop.get("name"), amount, currency, occurred_at)


class HistoricalLowSync:
    def __init__(
        self,
        provider: IsThereAnyDealProvider,
        session_factory: async_sessionmaker,
        interval_hours: int = 24,
        redis: Redis | None = None,
    ) -> None:
        self.provider = provider
        self.session_factory = session_factory
        self.interval_hours = interval_hours
        self.redis = redis

    async def get_low(self, game_id: int, country: str, scope: str = "steam") -> ExternalHistoricalLow | None:
        cache_key = f"itad:historical-low:{game_id}:{country}:{scope}"
        if self.redis and (cached := await self.redis.get(cache_key)):
            try:
                payload = json.loads(cached)
                return ExternalHistoricalLow(
                    game_id=game_id,
                    country_code=country,
                    scope=scope,
                    shop_name=payload.get("shop_name"),
                    price=Decimal(payload["price"]),
                    currency=payload["currency"],
                    occurred_at=datetime.fromisoformat(payload["occurred_at"]) if payload.get("occurred_at") else None,
                    updated_at=datetime.fromisoformat(payload["updated_at"]),
                )
            except (ValueError, KeyError, TypeError, InvalidOperation):
                pass
        async with self.session_factory() as session:
            saved = await session.scalar(
                select(ExternalHistoricalLow).where(
                    ExternalHistoricalLow.game_id == game_id,
                    ExternalHistoricalLow.country_code == country,
                    ExternalHistoricalLow.scope == scope,
                )
            )
        if saved:
            await self._cache_low(saved)
        return saved

    async def sync_game(self, game_id: int, app_id: int, country: str) -> int:
        mapping = await self.provider.lookup_steam_ids([app_id])
        external_id = mapping.get(app_id)
        if not external_id:
            return 0
        lows = await self.provider.historical_lows([external_id], country)
        return await self._save_lows({external_id: game_id}, country, lows, datetime.now(UTC))

    async def run(self) -> int:
        async with self.session_factory() as session:
            rules = list(
                (
                    await session.scalars(
                        select(WatchRule)
                        .join(User, User.id == WatchRule.user_id)
                        .options(selectinload(WatchRule.game), selectinload(WatchRule.user))
                        .where(
                            WatchRule.enabled.is_(True),
                            User.plan == Plan.PREMIUM,
                            User.premium_until > datetime.now(UTC),
                        )
                    )
                ).all()
            )
        games = {rule.game.steam_app_id: rule.game_id for rule in rules}
        mapping: dict[int, str] = {}
        for batch in self._batches(list(games), 200):
            mapping.update(await self.provider.lookup_steam_ids(batch))
        saved = 0
        now = datetime.now(UTC)
        for country in sorted({rule.user.country_code for rule in rules}):
            app_ids = {rule.game.steam_app_id for rule in rules if rule.user.country_code == country}
            reverse = {mapping[app_id]: games[app_id] for app_id in app_ids if app_id in mapping}
            for batch in self._batches(list(reverse), 200):
                try:
                    lows = await self.provider.historical_lows(batch, country)
                except ITADError:
                    log.exception("itad_historical_low_failed", country=country, games=len(batch))
                    continue
                saved += await self._save_lows(reverse, country, lows, now)
        log.info("itad_historical_low_sync_completed", saved=saved)
        return saved

    async def _save_lows(self, reverse: dict[str, int], country: str, lows: list[HistoricalLow], now: datetime) -> int:
        count = 0
        async with self.session_factory() as session:
            for low in lows:
                game_id = reverse.get(low.game_id)
                if not game_id:
                    continue
                values = dict(
                    game_id=game_id,
                    country_code=country,
                    scope=low.scope,
                    shop_name=low.shop_name,
                    price=low.price,
                    currency=low.currency,
                    occurred_at=low.occurred_at,
                    updated_at=now,
                )
                statement = pg_insert(ExternalHistoricalLow).values(**values)
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["game_id", "country_code", "scope"],
                        set_={
                            key: value
                            for key, value in values.items()
                            if key not in {"game_id", "country_code", "scope"}
                        },
                    )
                )
                await self._cache_low(ExternalHistoricalLow(**values))
                count += 1
            await session.commit()
        return count

    async def _cache_low(self, low: ExternalHistoricalLow) -> None:
        if not self.redis:
            return
        await self.redis.set(
            f"itad:historical-low:{low.game_id}:{low.country_code}:{low.scope}",
            json.dumps(
                {
                    "shop_name": low.shop_name,
                    "price": str(low.price),
                    "currency": low.currency,
                    "occurred_at": low.occurred_at.isoformat() if low.occurred_at else None,
                    "updated_at": low.updated_at.isoformat(),
                }
            ),
            ex=21_600,
        )

    @staticmethod
    def _batches(items: list, size: int):
        for start in range(0, len(items), size):
            yield items[start : start + size]
