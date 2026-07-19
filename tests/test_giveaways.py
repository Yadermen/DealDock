import httpx
import pytest

from steam_radar.services.giveaways import GamerPowerProvider, GiveawaySourceError


@pytest.mark.asyncio
async def test_giveaway_feed_keeps_full_games_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["platform"] == "steam"
        assert request.url.params["type"] == "game"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 42,
                    "title": "Free Game",
                    "type": "Game",
                    "open_giveaway_url": "https://example.com/free",
                    "image": "https://example.com/a.jpg",
                    "end_date": "2026-08-01T12:00:00Z",
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items = await GamerPowerProvider(client).fetch_games()
    assert len(items) == 1
    assert items[0].external_id == "gamerpower:42"


@pytest.mark.asyncio
async def test_giveaway_source_failure_is_explicit() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GiveawaySourceError):
            await GamerPowerProvider(client).fetch_games()


@pytest.mark.asyncio
async def test_empty_giveaway_feed() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(201))) as client:
        assert await GamerPowerProvider(client).fetch_games() == []
