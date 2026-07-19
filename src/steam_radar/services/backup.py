import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.engine import make_url

from steam_radar.config import Settings


def create_backup(settings: Settings) -> Path:
    directory = Path(settings.backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"steam_radar_{datetime.now(UTC):%Y-%m-%d_%H-%M-%S}.dump"
    url = make_url(settings.database_url.replace("postgresql+asyncpg", "postgresql"))
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password
    command = [
        "pg_dump",
        "--format=custom",
        "--file",
        str(target),
        "--host",
        url.host or "localhost",
        "--port",
        str(url.port or 5432),
        "--username",
        url.username or "postgres",
        url.database or "postgres",
    ]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=3600, check=False)
    if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump failed with exit code {result.returncode}")
    cutoff = datetime.now(UTC) - timedelta(days=settings.backup_retention_days)
    for item in directory.glob("steam_radar_*.dump"):
        if datetime.fromtimestamp(item.stat().st_mtime, UTC) < cutoff:
            item.unlink()
    return target
