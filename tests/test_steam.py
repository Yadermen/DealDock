import httpx
import pytest

from steam_radar.services.steam import SteamProvider


class FakeRedis:
    def __init__(self) -> None:
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, **kwargs):
        self.values[key] = value


def test_extract_app_id_from_url() -> None:
    assert SteamProvider.extract_app_id("https://store.steampowered.com/app/620/Portal_2/") == 620


def test_extract_app_id_from_number() -> None:
    assert SteamProvider.extract_app_id("620") == 620


def test_regular_title_is_not_an_app_id() -> None:
    assert SteamProvider.extract_app_id("Portal 2") is None


@pytest.mark.asyncio
async def test_details_loads_and_normalizes_price_immediately() -> None:
    payload = {
        "620": {
            "success": True,
            "data": {
                "name": "Portal 2",
                "type": "game",
                "is_free": False,
                "price_overview": {"currency": "KZT", "initial": 359900, "final": 359900, "discount_percent": 100},
            },
        }
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        _, price = await SteamProvider(FakeRedis(), client).details(620, "KZ", force_refresh=True)
    assert price.initial == 3599
    assert price.final == 0
    assert price.discount_percent == 100


@pytest.mark.asyncio
async def test_steam_unavailable_raises_domain_error() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
        with pytest.raises(Exception, match="Steam Store is unavailable"):
            await SteamProvider(FakeRedis(), client).details(620, "KZ", force_refresh=True)
