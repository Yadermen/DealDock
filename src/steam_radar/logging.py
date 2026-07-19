import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog


def configure_logging(level: str, log_dir: str = "logs", max_bytes: int = 10_485_760, backup_count: int = 10) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S%z")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        directory / "steam-radar.log", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logging.basicConfig(level=numeric_level, handlers=[console, file_handler], force=True)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
    )
