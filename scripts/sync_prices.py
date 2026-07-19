"""Run the watched-price synchronization once for diagnostics or operations."""

import asyncio

import httpx
from aiogram import Bot
from redis.asyncio import Redis

from steam_radar.config import get_settings
from steam_radar.db import create_session_factory
from steam_radar.services.monitor import PriceMonitor
from steam_radar.services.steam import SteamProvider


async def main() -> None:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    bot = Bot(settings.bot_token)
    session_factory = create_session_factory(settings.database_url)
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "SteamRadar/0.1"}) as client:
        monitor = PriceMonitor(bot, session_factory, SteamProvider(redis, client), settings)
        count = await monitor.run(force=True)
        print(f"Synchronized watch rules: {count}")
    await bot.session.close()
    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
