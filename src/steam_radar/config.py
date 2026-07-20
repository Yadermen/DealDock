from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    database_url: str = "postgresql+asyncpg://steam_radar:steam_radar@localhost/steam_radar"
    redis_url: str = "redis://localhost:6379/0"
    admin_ids: frozenset[int] = Field(default_factory=frozenset)
    log_level: str = "INFO"
    free_check_hours: int = 24
    premium_check_hours: int = 1
    steam_request_delay: float = 1.0
    app_timezone: str = "Europe/Warsaw"
    app_env: str = "development"
    telegram_payment_test_mode: bool = False
    log_dir: str = "logs"
    log_max_bytes: int = 10_485_760
    log_backup_count: int = 10
    health_host: str = "0.0.0.0"
    health_port: int = 8080
    polling_lock_ttl: int = 90
    monitor_batch_size: int = 100
    deal_broadcast_price_max_age_hours: int = 26
    deal_broadcast_batch_size: int = 50
    deal_broadcast_messages_per_second: int = 20
    backup_enabled: bool = False
    backup_interval_hours: int = 24
    backup_retention_days: int = 14
    backup_dir: str = "backups"
    itad_api_key: str = ""
    itad_sync_hours: int = 24

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> frozenset[int]:
        if isinstance(value, str):
            return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
        return frozenset(value or [])

    @field_validator(
        "free_check_hours",
        "premium_check_hours",
        "log_max_bytes",
        "log_backup_count",
        "polling_lock_ttl",
        "monitor_batch_size",
        "deal_broadcast_price_max_age_hours",
        "deal_broadcast_batch_size",
        "deal_broadcast_messages_per_second",
        "backup_interval_hours",
        "backup_retention_days",
        "itad_sync_hours",
    )
    @classmethod
    def positive_integer(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be greater than zero")
        return value

    @field_validator("steam_request_delay")
    @classmethod
    def non_negative_delay(cls, value: float) -> float:
        if value < 0:
            raise ValueError("STEAM_REQUEST_DELAY cannot be negative")
        return value

    @field_validator("health_port")
    @classmethod
    def valid_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("HEALTH_PORT must be between 1 and 65535")
        return value

    @field_validator("app_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("APP_TIMEZONE must be a valid IANA time zone") from error
        return value

    @model_validator(mode="after")
    def validate_environment(self) -> "Settings":
        if self.app_env not in {"development", "production", "test"}:
            raise ValueError("APP_ENV must be development, production or test")
        if self.app_env == "production" and self.telegram_payment_test_mode:
            raise ValueError("TELEGRAM_PAYMENT_TEST_MODE cannot be enabled in production")
        if self.app_env == "production" and not self.is_configured:
            raise ValueError("BOT_TOKEN is required in production")
        return self

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and ":" in self.bot_token)


@lru_cache
def get_settings() -> Settings:
    return Settings()
