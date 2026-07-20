from datetime import UTC
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from steam_radar.services.itad import HistoricalLowSync, IsThereAnyDealProvider


@pytest.mark.asyncio
async def test_lookup_and_historical_lows_keep_steam_separate() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["ITAD-API-Key"] == "secret"
        if "/lookup/" in request.url.path:
            return httpx.Response(200, json={"app/252490": "game-uuid"})
        if "historylow" in request.url.path:
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "game-uuid",
                        "low": {
                            "shop": {"id": 47, "name": "Another Store"},
                            "price": {"amount": 5.5, "currency": "PLN"},
                            "timestamp": "2025-01-01T00:00:00+00:00",
                        },
                    }
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "id": "game-uuid",
                    "lows": [
                        {
                            "shop": {"id": 61, "name": "Steam"},
                            "price": {"amount": 8.99, "currency": "PLN"},
                            "timestamp": "2025-02-01T00:00:00+00:00",
                        }
                    ],
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = IsThereAnyDealProvider(client, "secret")
        assert await provider.lookup_steam_ids([252490]) == {252490: "game-uuid"}
        lows = await provider.historical_lows(["game-uuid"], "PL")
    assert {(low.scope, low.price, low.shop_name) for low in lows} == {
        ("all_stores", Decimal("5.5"), "Another Store"),
        ("steam", Decimal("8.99"), "Steam"),
    }
    assert all(low.occurred_at.tzinfo == UTC for low in lows)


def test_invalid_external_price_is_rejected() -> None:
    assert IsThereAnyDealProvider._parse_low("id", "steam", {"price": {"amount": -1, "currency": "USD"}}) is None


@pytest.mark.asyncio
async def test_historical_low_uses_redis_before_database() -> None:
    class Redis:
        async def get(self, key):
            return (
                '{"shop_name":"Steam","price":"79.99","currency":"PLN",'
                '"occurred_at":null,"updated_at":"2026-07-19T12:00:00+00:00"}'
            )

    class ForbiddenFactory:
        def __call__(self):
            raise AssertionError("database should not be used on Redis hit")

    service = HistoricalLowSync(SimpleNamespace(), ForbiddenFactory(), redis=Redis())
    low = await service.get_low(1, "PL")
    assert low.price == Decimal("79.99") and low.currency == "PLN"
