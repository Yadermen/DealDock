from dataclasses import dataclass
from datetime import UTC, datetime

import httpx


class GiveawaySourceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExternalGiveaway:
    external_id: str
    title: str
    url: str
    store: str
    image_url: str | None
    ends_at: datetime | None


class GamerPowerProvider:
    URL = "https://www.gamerpower.com/api/filter"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def fetch_games(self) -> list[ExternalGiveaway]:
        try:
            response = await self.client.get(self.URL, params={"platform": "steam", "type": "game"})
            if response.status_code == 201:
                return []
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise GiveawaySourceError(f"GamerPower is unavailable: {error}") from error
        if not isinstance(payload, list):
            raise GiveawaySourceError("GamerPower returned an unexpected response")
        result: list[ExternalGiveaway] = []
        for item in payload:
            if not isinstance(item, dict) or not item.get("id") or not item.get("title"):
                continue
            # The API type=game feed contains limited promotions, not permanent Steam F2P catalog.
            result.append(
                ExternalGiveaway(
                    external_id=f"gamerpower:{item['id']}",
                    title=str(item["title"]),
                    url=str(item.get("open_giveaway_url") or item.get("gamerpower_url") or ""),
                    store="Steam",
                    image_url=item.get("image"),
                    ends_at=_parse_date(item.get("end_date")),
                )
            )
        return [item for item in result if item.url.startswith("https://")]


def _parse_date(value: object) -> datetime | None:
    if not value or value == "N/A":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None
