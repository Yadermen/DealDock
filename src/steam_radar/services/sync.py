from datetime import UTC, datetime

import structlog
from redis.asyncio import Redis
from redis.exceptions import LockError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from steam_radar.db.models import Giveaway, SyncRun, SystemError
from steam_radar.services.giveaways import GamerPowerProvider
from steam_radar.services.monitor import PriceMonitor

log = structlog.get_logger()


class SyncCoordinator:
    STATUS_KEY = "sync:full:status"
    GIVEAWAY_UPDATED_KEY = "sync:giveaways:last_success"

    def __init__(
        self, redis: Redis, session_factory: async_sessionmaker, monitor: PriceMonitor, giveaways: GamerPowerProvider
    ) -> None:
        self.redis = redis
        self.session_factory = session_factory
        self.monitor = monitor
        self.giveaways = giveaways

    async def full_sync(self) -> bool:
        lock = self.redis.lock("lock:sync:full", timeout=1800, blocking_timeout=0)
        if not await lock.acquire():
            log.info("initial_sync_skipped", reason="already_running")
            return False
        run_id = await self._start_run("full")
        await self.redis.set(self.STATUS_KEY, "running", ex=3600)
        log.info("initial_sync_started", run_id=run_id)
        processed = errors = 0
        try:
            processed += await self.monitor.run(force=True)
            processed += await self.sync_giveaways()
            await self._finish_run(run_id, "success", processed, errors)
            await self.redis.set(self.STATUS_KEY, "success", ex=86_400)
            log.info("initial_sync_finished", run_id=run_id, processed=processed)
            return True
        except Exception as error:
            errors += 1
            await self.record_error("full_sync", error)
            await self._finish_run(run_id, "failed", processed, errors)
            await self.redis.set(self.STATUS_KEY, "failed", ex=3600)
            log.exception("initial_sync_failed", run_id=run_id)
            return False
        finally:
            try:
                await lock.release()
            except LockError:
                pass

    async def sync_giveaways(self) -> int:
        lock = self.redis.lock("lock:sync:giveaways", timeout=300, blocking_timeout=0)
        if not await lock.acquire():
            return 0
        try:
            items = await self.giveaways.fetch_games()
            now = datetime.now(UTC)
            external_ids = {item.external_id for item in items}
            async with self.session_factory() as session:
                await session.execute(update(Giveaway).where(Giveaway.source == "gamerpower").values(active=False))
                for item in items:
                    giveaway = await session.scalar(select(Giveaway).where(Giveaway.external_id == item.external_id))
                    if giveaway is None:
                        giveaway = Giveaway(
                            external_id=item.external_id,
                            source="gamerpower",
                            title=item.title,
                            url=item.url,
                            store=item.store,
                            kind=item.kind,
                            approved=True,
                        )
                        session.add(giveaway)
                    giveaway.title, giveaway.url = item.title, item.url
                    giveaway.kind = item.kind
                    giveaway.image_url, giveaway.ends_at = item.image_url, item.ends_at
                    giveaway.approved, giveaway.active, giveaway.last_seen_at = True, True, now
                await session.commit()
            await self.redis.set(self.GIVEAWAY_UPDATED_KEY, now.isoformat(), ex=604_800)
            log.info("giveaway_sync_finished", count=len(external_ids))
            return len(external_ids)
        except Exception as error:
            await self.record_error("giveaway_sync", error)
            log.warning("giveaway_sync_failed", error=str(error))
            raise
        finally:
            try:
                await lock.release()
            except LockError:
                pass

    async def record_error(self, component: str, error: Exception) -> None:
        async with self.session_factory() as session:
            session.add(SystemError(component=component, message=str(error)[:2000], occurred_at=datetime.now(UTC)))
            await session.commit()

    async def _start_run(self, kind: str) -> int:
        async with self.session_factory() as session:
            run = SyncRun(kind=kind, status="running", started_at=datetime.now(UTC))
            session.add(run)
            await session.commit()
            return run.id

    async def _finish_run(self, run_id: int, status: str, processed: int, errors: int) -> None:
        async with self.session_factory() as session:
            run = await session.get(SyncRun, run_id)
            run.status, run.finished_at = status, datetime.now(UTC)
            run.processed, run.error_count = processed, errors
            await session.commit()
