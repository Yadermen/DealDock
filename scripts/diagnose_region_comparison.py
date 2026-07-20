"""Diagnose the latest configured regional comparison without exposing secrets."""

import asyncio

import httpx
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from steam_radar.config import get_settings
from steam_radar.constants import REGIONS
from steam_radar.db import create_session_factory
from steam_radar.db.models import User, WatchRule
from steam_radar.services.steam import SteamProvider


async def main() -> None:
    settings = get_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        user = await session.scalar(
            select(User)
            .where(User.comparison_regions.is_not(None))
            .order_by(User.last_seen_at.desc().nulls_last())
            .limit(1)
        )
        if not user or not user.comparison_regions:
            raise SystemExit("No saved regional comparison was found")
        rule = await session.scalar(
            select(WatchRule)
            .options(selectinload(WatchRule.game))
            .where(WatchRule.user_id == user.id, WatchRule.enabled.is_(True))
            .order_by(WatchRule.updated_at.desc())
            .limit(1)
        )
    if not rule:
        raise SystemExit("No tracked game was found")
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    print(f"app_id={rule.game.steam_app_id} game={rule.game.name!r}")
    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "SteamRadar/0.1"},
        trust_env=False,
    ) as client:
        steam = SteamProvider(redis, client)
        for code in user.comparison_regions:
            region = REGIONS.get(code)
            if not region:
                print(f"region={code} error=unsupported_region")
                continue
            try:
                _, price = await steam.details(
                    rule.game.steam_app_id,
                    region.steam_country_code,
                    "russian",
                    force_refresh=True,
                )
                if price:
                    print(
                        f"region={code} country={region.steam_country_code} status=ok "
                        f"currency={price.currency} final={price.final}"
                    )
                else:
                    print(f"region={code} country={region.steam_country_code} status=no_price")
            except Exception as error:
                cause = error.__cause__
                print(
                    f"region={code} country={region.steam_country_code} status=error "
                    f"type={type(error).__name__} http={getattr(error, 'status_code', None)} "
                    f"transient={getattr(error, 'transient', False)} message={error} "
                    f"cause={type(cause).__name__ if cause else None}:{cause}"
                )
    await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
