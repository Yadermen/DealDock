import asyncio
import re

import httpx
import structlog

from steam_radar.services.steam import SteamError, SteamGame, SteamProvider

log = structlog.get_logger()
_STEAM_APP = re.compile(r"store\.steampowered\.com/app/(\d+)")


class RawgSearchProvider:
    """Optional alias discovery. Every result is mapped back to and verified by Steam."""

    BASE_URL = "https://api.rawg.io/api"

    def __init__(self, client: httpx.AsyncClient, api_key: str, steam: SteamProvider) -> None:
        self.client = client
        self.api_key = api_key
        self.steam = steam

    async def search(self, query: str, country: str, language: str, limit: int = 3) -> list[SteamGame]:
        if not self.api_key:
            return []
        try:
            response = await self.client.get(
                f"{self.BASE_URL}/games",
                params={"key": self.api_key, "search": query, "search_precise": "false", "page_size": limit},
                timeout=8,
            )
            response.raise_for_status()
            candidates = response.json().get("results", [])[:limit]
            resolved = await asyncio.gather(
                *(self._resolve(item["id"], country, language) for item in candidates if item.get("id")),
                return_exceptions=True,
            )
            return [item for item in resolved if isinstance(item, SteamGame)]
        except httpx.HTTPError as error:
            log.warning("rawg_search_failed", error_type=type(error).__name__)
            return []

    async def _resolve(self, rawg_id: int, country: str, language: str) -> SteamGame | None:
        response = await self.client.get(
            f"{self.BASE_URL}/games/{rawg_id}/stores",
            params={"key": self.api_key},
            timeout=8,
        )
        response.raise_for_status()
        for entry in response.json().get("results", []):
            match = _STEAM_APP.search(str(entry.get("url", "")))
            if not match:
                continue
            try:
                game, _ = await self.steam.details(int(match.group(1)), country, language)
                return game
            except SteamError:
                return None
        return None
