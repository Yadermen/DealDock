import asyncio

import httpx
from redis.asyncio import Redis

from steam_radar.config import get_settings
from steam_radar.db import create_session_factory
from steam_radar.services.steam_catalog import SteamCatalogService


async def main() -> None:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with httpx.AsyncClient(headers={"User-Agent": "DealDock/0.1"}, trust_env=False) as client:
            count = await SteamCatalogService(
                client,
                redis,
                create_session_factory(settings.database_url),
                settings.steam_web_api_key,
            ).sync()
            print(f"Steam catalogue synchronized: {count} apps")
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
