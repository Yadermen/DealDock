from apscheduler.schedulers.asyncio import AsyncIOScheduler

from steam_radar.services.digest import DigestService
from steam_radar.services.giveaway_notifications import GiveawayNotificationService
from steam_radar.services.itad import HistoricalLowSync
from steam_radar.services.monitor import PriceMonitor
from steam_radar.services.premium import PremiumService
from steam_radar.services.sync import SyncCoordinator


def create_scheduler(
    monitor: PriceMonitor,
    sync: SyncCoordinator,
    premium: PremiumService | None = None,
    digest: DigestService | None = None,
    historical_lows: HistoricalLowSync | None = None,
    giveaway_notifications: GiveawayNotificationService | None = None,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        monitor.run, "interval", minutes=15, id="price-monitor", max_instances=1, coalesce=True, misfire_grace_time=300
    )
    scheduler.add_job(
        sync.sync_giveaways,
        "interval",
        hours=1,
        id="giveaway-sync",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    scheduler.add_job(
        monitor.send_deferred, "interval", minutes=5, id="deferred-notifications", max_instances=1, coalesce=True
    )
    if premium:
        scheduler.add_job(
            premium.expire_subscriptions,
            "interval",
            minutes=15,
            id="premium-expiration",
            max_instances=1,
            coalesce=True,
        )
    if digest:
        scheduler.add_job(digest.run, "interval", minutes=15, id="digests", max_instances=1, coalesce=True)
    if historical_lows:
        scheduler.add_job(
            historical_lows.run,
            "interval",
            hours=historical_lows.interval_hours,
            id="external-historical-lows",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
    if giveaway_notifications:
        scheduler.add_job(
            giveaway_notifications.run,
            "interval",
            minutes=15,
            id="giveaway-notifications",
            max_instances=1,
            coalesce=True,
        )
    return scheduler
