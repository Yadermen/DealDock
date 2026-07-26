import json
import re
from collections.abc import Iterable

import httpx
import structlog
from redis.asyncio import Redis
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from steam_radar.db.models import SteamCatalogApp

log = structlog.get_logger()

_WORDS = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOPWORDS = {"a", "an", "and", "of", "the", "edition", "remastered", "definitive", "complete"}
_ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}


def build_search_text(name: str) -> str:
    """Generate searchable title forms such as gta sa, gtasa, rdr2 and gta5."""
    words = [word.casefold() for word in _WORDS.findall(name)]
    significant = [word for word in words if word not in _STOPWORDS]
    aliases = {" ".join(words), " ".join(significant)}
    numeric = [_ROMAN.get(word, word) for word in significant]
    aliases.add(" ".join(numeric))

    # Initials of every meaningful prefix produce generic abbreviations (GTA, RDR).
    for end in range(2, len(significant) + 1):
        initials = "".join(word[0] for word in significant[:end])
        if len(initials) >= 2:
            aliases.add(initials)

    # Split a title into franchise/subtitle around punctuation and combine their initials.
    parts = [part for part in re.split(r"[:\-–—]", name) if part.strip()]
    part_aliases: list[str] = []
    for part in parts:
        part_words = [w.casefold() for w in _WORDS.findall(part) if w.casefold() not in _STOPWORDS]
        if part_words:
            part_aliases.append("".join(_ROMAN.get(w, w)[0] for w in part_words))
    if len(part_aliases) > 1:
        aliases.add(" ".join(part_aliases))
        aliases.add("".join(part_aliases))

    # Common title shape: Grand Theft Auto San Andreas -> GTA SA.
    for split in range(2, len(significant)):
        left = "".join(word[0] for word in significant[:split])
        right = "".join(_ROMAN.get(word, word)[0] for word in significant[split:])
        if len(left) >= 2 and right:
            aliases.update({f"{left} {right}", f"{left}{right}"})

    compact_numeric = "".join(word[0] if word not in _ROMAN else _ROMAN[word] for word in significant)
    if len(compact_numeric) >= 2:
        aliases.add(compact_numeric)
    return " | ".join(sorted(alias for alias in aliases if alias))


class SteamCatalogService:
    APPLIST_URL = "https://partner.steam-api.com/IStoreService/GetAppList/v1/"

    def __init__(
        self,
        client: httpx.AsyncClient,
        redis: Redis,
        session_factory: async_sessionmaker,
        api_key: str,
        batch_size: int = 2_000,
    ) -> None:
        self.client = client
        self.redis = redis
        self.session_factory = session_factory
        self.api_key = api_key
        self.batch_size = batch_size

    async def sync(self) -> int:
        if not self.api_key:
            log.info("steam_catalog_sync_skipped", reason="STEAM_WEB_API_KEY is not configured")
            return 0
        lock = self.redis.lock("lock:steam-catalog-sync", timeout=3_600, blocking_timeout=0)
        if not await lock.acquire(blocking=False):
            return 0
        try:
            processed = 0
            last_appid = 0
            while True:
                request = {
                    "include_games": True,
                    "include_dlc": False,
                    "include_software": False,
                    "include_videos": False,
                    "include_hardware": False,
                    "last_appid": last_appid,
                    "max_results": 50_000,
                }
                response = await self.client.get(
                    self.APPLIST_URL,
                    params={"key": self.api_key, "input_json": json.dumps(request)},
                    timeout=60,
                )
                response.raise_for_status()
                payload = response.json().get("response", {})
                apps = payload.get("apps", [])
                if not apps:
                    break
                processed += await self._upsert(apps)
                next_appid = int(payload.get("last_appid") or apps[-1]["appid"])
                if not payload.get("have_more_results") or next_appid <= last_appid:
                    break
                last_appid = next_appid
            log.info("steam_catalog_synced", apps=processed)
            return processed
        finally:
            try:
                await lock.release()
            except Exception:
                log.warning("steam_catalog_lock_release_failed")

    async def _upsert(self, apps: list[dict]) -> int:
        processed = 0
        for batch in _chunks(apps, self.batch_size):
                rows = [
                    {
                        "steam_app_id": int(app["appid"]),
                        "name": str(app["name"]).strip()[:300],
                        "search_text": build_search_text(str(app["name"])),
                    }
                    for app in batch
                    if app.get("appid") and str(app.get("name", "")).strip()
                ]
                if not rows:
                    continue
                statement = insert(SteamCatalogApp).values(rows)
                statement = statement.on_conflict_do_update(
                    index_elements=[SteamCatalogApp.steam_app_id],
                    set_={"name": statement.excluded.name, "search_text": statement.excluded.search_text},
                )
                async with self.session_factory() as session:
                    await session.execute(statement)
                    await session.commit()
                processed += len(rows)
        return processed


def _chunks(items: list[dict], size: int) -> Iterable[list[dict]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
