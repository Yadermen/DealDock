"""Synchronize IsThereAnyDeal historical lows once."""

import asyncio

import httpx

from steam_radar.config import get_settings
from steam_radar.db import create_session_factory
from steam_radar.services.itad import HistoricalLowSync, IsThereAnyDealProvider


async def main() -> None:
    settings = get_settings()
    if not settings.itad_api_key:
        raise SystemExit("ITAD_API_KEY is not configured")
    session_factory = create_session_factory(settings.database_url)
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "SteamRadar/0.1"}) as client:
        service = HistoricalLowSync(
            IsThereAnyDealProvider(client, settings.itad_api_key),
            session_factory,
            settings.itad_sync_hours,
        )
        count = await service.run()
        print(f"Synchronized external historical lows: {count}")


if __name__ == "__main__":
    asyncio.run(main())
