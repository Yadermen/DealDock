import asyncio
import sys

import httpx
from redis.asyncio import Redis

from steam_radar.config import get_settings
from steam_radar.db import create_session_factory
from steam_radar.services.game_search import GameSearchService
from steam_radar.services.itad import IsThereAnyDealProvider
from steam_radar.services.rawg import RawgSearchProvider
from steam_radar.services.steam import SteamProvider


async def main(queries: list[str]) -> None:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with httpx.AsyncClient(headers={"User-Agent": "DealDock/0.1"}, timeout=15, trust_env=False) as client:
            steam = SteamProvider(redis, client)
            itad = IsThereAnyDealProvider(client, settings.itad_api_key) if settings.itad_api_key else None
            rawg = RawgSearchProvider(client, settings.rawg_api_key, steam) if settings.rawg_api_key else None
            search = GameSearchService(create_session_factory(settings.database_url), steam, itad, rawg)
            for query in queries:
                games = await search.search(query, "KZ", "english")
                print(query, [(game.app_id, game.name) for game in games[:3]])
    finally:
        await redis.aclose()


if __name__ == "__main__":
    queries = sys.argv[1:] or ["rdr2", "gtasa", "re2", "dmc5"]
    if queries == ["--cyrillic"]:
        queries = ["гта са", "ред дед редемпшен 2", "девил мей край 5", "резидент ивел 2"]
    elif queries == ["--rust-ru"]:
        queries = ["раст"]
    asyncio.run(main(queries))
