import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime

import structlog
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

log = structlog.get_logger()


class PollingLock:
    def __init__(self, redis: Redis, key: str, ttl: int = 90) -> None:
        self.redis, self.key, self.ttl = redis, key, ttl
        self.lock = redis.lock(key, timeout=ttl, blocking_timeout=0)
        self._renew_task: asyncio.Task | None = None

    async def acquire(self) -> bool:
        if not await self.lock.acquire():
            return False
        self._renew_task = asyncio.create_task(self._renew(), name="polling-lock-renew")
        return True

    async def _renew(self) -> None:
        while True:
            await asyncio.sleep(max(5, self.ttl // 3))
            try:
                await self.lock.extend(self.ttl, replace_ttl=True)
            except Exception:
                log.exception("polling_lock_renew_failed")
                return

    async def release(self) -> None:
        if self._renew_task:
            self._renew_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._renew_task
        with suppress(Exception):
            await self.lock.release()


class HealthServer:
    def __init__(self, host: str, port: int, session_factory: async_sessionmaker, redis: Redis, scheduler) -> None:
        self.host, self.port = host, port
        self.session_factory, self.redis, self.scheduler = session_factory, redis, scheduler
        self.server: asyncio.Server | None = None

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, self.host, self.port)
        log.info("health_server_started", host=self.host, port=self.port)

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def status(self) -> tuple[int, dict]:
        result = {
            "status": "ok",
            "database": "ok",
            "redis": "ok",
            "scheduler": "ok" if self.scheduler.running else "error",
            "checked_at": datetime.now(UTC).isoformat(),
        }
        try:
            async with self.session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            result["database"] = "error"
        try:
            if not await self.redis.ping():
                result["redis"] = "error"
            result["last_successful_sync"] = await self.redis.get("sync:giveaways:last_success")
        except Exception:
            result["redis"] = "error"
        if "error" in result.values():
            result["status"] = "error"
            return 503, result
        return 200, result

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.readline(), timeout=2)
            if not request.startswith(b"GET /health "):
                status, payload = 404, {"status": "not_found"}
            else:
                status, payload = await self.status()
            body = json.dumps(payload).encode()
            reason = "OK" if status == 200 else "Service Unavailable" if status == 503 else "Not Found"
            writer.write(
                f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
