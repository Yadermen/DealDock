import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal

import httpx
import structlog
from redis.asyncio import Redis

from steam_radar.services.pricing import InvalidPrice, normalize_steam_price

log = structlog.get_logger()


class SteamError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, transient: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.transient = transient


@dataclass(slots=True)
class SteamGame:
    app_id: int
    name: str
    header_image: str | None
    game_type: str
    is_free: bool


@dataclass(slots=True)
class SteamPrice:
    app_id: int
    currency: str
    initial: Decimal
    final: Decimal
    discount_percent: int


class SteamProvider:
    APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
    SEARCH_URL = "https://store.steampowered.com/api/storesearch"
    APP_ID_RE = re.compile(r"(?:store\.steampowered\.com/app/)?(\d{2,10})")

    def __init__(self, redis: Redis, client: httpx.AsyncClient) -> None:
        self.redis = redis
        self.client = client

    async def search(self, query: str, country: str, language: str = "russian") -> list[SteamGame]:
        direct_id = self.extract_app_id(query)
        if direct_id:
            game, _ = await self.details(direct_id, country, language)
            return [game]
        key = f"steam:search:{country}:{language}:{query.casefold()}"
        if cached := await self.redis.get(key):
            return [SteamGame(**item) for item in json.loads(cached)]
        try:
            response = await self.client.get(
                self.SEARCH_URL,
                params={"term": query, "cc": country, "l": language},
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise SteamError(f"Steam search is unavailable: {error}") from error
        items = response.json().get("items", [])[:8]
        games = [
            SteamGame(
                app_id=int(item["id"]),
                name=item["name"],
                header_image=item.get("tiny_image"),
                game_type="game",
                is_free=False,
            )
            for item in items
        ]
        await self.redis.set(key, json.dumps([asdict(game) for game in games]), ex=21_600)
        return games

    async def details(
        self, app_id: int, country: str, language: str = "russian", force_refresh: bool = False
    ) -> tuple[SteamGame, SteamPrice | None]:
        country = country.upper()
        if not re.fullmatch(r"[A-Z]{2}", country):
            raise SteamError("Invalid two-letter Steam country code", status_code=400)
        key = f"steam:details:{app_id}:{country}:{language}"
        if not force_refresh and (cached := await self.redis.get(key)):
            payload = json.loads(cached)
        else:
            try:
                response = await self.client.get(
                    self.APP_DETAILS_URL,
                    params={"appids": app_id, "cc": country, "l": language},
                )
                response.raise_for_status()
                wrapper = response.json().get(str(app_id), {})
            except httpx.HTTPStatusError as error:
                status = error.response.status_code
                raise SteamError(
                    f"Steam Store is unavailable: HTTP {status}",
                    status_code=status,
                    transient=status == 429 or 500 <= status < 600,
                ) from error
            except httpx.TimeoutException as error:
                raise SteamError("Steam Store timed out", transient=True) from error
            except (httpx.RequestError, ValueError) as error:
                raise SteamError("Steam Store is temporarily unavailable", transient=True) from error
            if not wrapper.get("success"):
                raise SteamError("Steam returned success=false", status_code=404)
            payload = wrapper["data"]
            await self.redis.set(key, json.dumps(payload), ex=3600)
        game = SteamGame(
            app_id=app_id,
            name=payload.get("name", f"App {app_id}"),
            header_image=payload.get("header_image"),
            game_type=payload.get("type", "game"),
            is_free=bool(payload.get("is_free", False)),
        )
        price_data = payload.get("price_overview")
        if not price_data:
            log.info("steam_price_unavailable", steam_app_id=app_id, country_code=country)
            return game, None
        try:
            normalized = normalize_steam_price(price_data)
        except InvalidPrice as error:
            raise SteamError(str(error)) from error
        price = SteamPrice(
            app_id=app_id,
            currency=normalized.currency,
            initial=normalized.initial,
            final=normalized.final,
            discount_percent=normalized.discount_percent,
        )
        return game, price

    @classmethod
    def extract_app_id(cls, value: str) -> int | None:
        match = cls.APP_ID_RE.search(value.strip())
        return int(match.group(1)) if match and (value.strip().isdigit() or "steam" in value) else None
