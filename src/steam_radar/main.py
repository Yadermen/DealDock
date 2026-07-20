import asyncio
import hashlib

import httpx
import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats
from redis.asyncio import Redis

from steam_radar.bot import admin, handlers
from steam_radar.bot.middlewares import ActivityMiddleware, CommandCleanupMiddleware
from steam_radar.config import get_settings
from steam_radar.db import create_session_factory
from steam_radar.i18n import text
from steam_radar.logging import configure_logging
from steam_radar.scheduler import create_scheduler
from steam_radar.services.backup import create_backup
from steam_radar.services.currency import CurrencyService
from steam_radar.services.deal_broadcast import DealBroadcastService
from steam_radar.services.digest import DigestService
from steam_radar.services.giveaway_notifications import GiveawayNotificationService
from steam_radar.services.giveaways import GamerPowerProvider
from steam_radar.services.itad import HistoricalLowSync, IsThereAnyDealProvider
from steam_radar.services.monitor import PriceMonitor
from steam_radar.services.premium import PremiumService
from steam_radar.services.runtime import HealthServer, PollingLock
from steam_radar.services.steam import SteamProvider
from steam_radar.services.sync import SyncCoordinator


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_dir, settings.log_max_bytes, settings.log_backup_count)
    if not settings.is_configured:
        raise RuntimeError("BOT_TOKEN is missing or invalid. Copy .env.example to .env and set it.")

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    storage = RedisStorage(redis=redis)
    bot_session = None
    if settings.telegram_payment_test_mode:
        # Telegram's test DC is a separate API endpoint and requires a bot token created there.
        # Merely marking a local transaction as test must never send a real Stars invoice.
        bot_session = AiohttpSession(
            api=TelegramAPIServer(
                base="https://api.telegram.org/bot{token}/test/{method}",
                file="https://api.telegram.org/file/bot{token}/test/{path}",
            )
        )
    bot = Bot(settings.bot_token, session=bot_session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher(storage=storage)
    dispatcher.update.outer_middleware(CommandCleanupMiddleware())
    dispatcher.update.outer_middleware(ActivityMiddleware())
    dispatcher.include_router(admin.router)
    dispatcher.include_router(handlers.router)
    session_factory = create_session_factory(settings.database_url)
    lock_key = "lock:telegram-polling:" + hashlib.sha256(settings.bot_token.encode()).hexdigest()[:16]
    polling_lock = PollingLock(redis, lock_key, settings.polling_lock_ttl)
    if not await polling_lock.acquire():
        raise RuntimeError("Another polling instance is already running for this bot token")

    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "SteamRadar/0.1"},
        trust_env=False,
    ) as client:
        steam = SteamProvider(redis, client)
        currency = CurrencyService(redis, client, session_factory=session_factory)
        monitor = PriceMonitor(bot, session_factory, steam, settings)
        giveaway_provider = GamerPowerProvider(client)
        sync = SyncCoordinator(redis, session_factory, monitor, giveaway_provider)
        premium_service = PremiumService(bot, session_factory)
        digest_service = DigestService(bot, session_factory)
        deal_broadcast_service = DealBroadcastService(bot, session_factory, redis, settings)
        historical_lows = (
            HistoricalLowSync(
                IsThereAnyDealProvider(client, settings.itad_api_key),
                session_factory,
                settings.itad_sync_hours,
                redis,
            )
            if settings.itad_api_key
            else None
        )
        giveaway_notifications = GiveawayNotificationService(bot, session_factory)
        scheduler = create_scheduler(
            monitor,
            sync,
            premium_service,
            digest_service,
            historical_lows,
            giveaway_notifications,
        )
        if settings.backup_enabled:

            async def scheduled_backup() -> None:
                await asyncio.to_thread(create_backup, settings)

            scheduler.add_job(
                scheduled_backup,
                "interval",
                hours=settings.backup_interval_hours,
                id="postgres-backup",
                max_instances=1,
                coalesce=True,
            )
        scheduler.start()
        health = HealthServer(settings.health_host, settings.health_port, session_factory, redis, scheduler)
        await health.start()
        initial_sync = asyncio.create_task(sync.full_sync(), name="initial-sync")
        initial_historical_sync = (
            asyncio.create_task(historical_lows.run(), name="initial-historical-low-sync") if historical_lows else None
        )
        await deal_broadcast_service.resume_active()
        try:
            command_names = ("start", "menu", "games", "watch", "deals", "free", "premium", "settings", "help", "terms")
            for language in ("ru", "en", "uk", "pl"):
                await bot.set_my_commands(
                    [BotCommand(command=name, description=text(language, f"command_{name}")) for name in command_names],
                    scope=BotCommandScopeAllPrivateChats(),
                    language_code=language,
                )
            await bot.set_my_commands(
                [BotCommand(command=name, description=text("ru", f"command_{name}")) for name in command_names],
                scope=BotCommandScopeAllPrivateChats(),
            )
            await bot.delete_webhook(drop_pending_updates=False)
            await dispatcher.start_polling(
                bot,
                settings=settings,
                session_factory=session_factory,
                redis=redis,
                steam=steam,
                currency=currency,
                historical_lows=historical_lows,
                sync=sync,
            )
        finally:
            if not initial_sync.done():
                initial_sync.cancel()
            if initial_historical_sync and not initial_historical_sync.done():
                initial_historical_sync.cancel()
            scheduler.shutdown(wait=False)
            await health.close()
            await polling_lock.release()
            await redis.aclose()
            await bot.session.close()


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        structlog.get_logger().info("shutdown")


if __name__ == "__main__":
    run()
