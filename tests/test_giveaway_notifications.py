from datetime import UTC, datetime
from types import SimpleNamespace

from steam_radar.db.models import GiveawayKind
from steam_radar.services.giveaway_notifications import GiveawayNotificationService


def giveaway(kind: GiveawayKind = GiveawayKind.KEEP):
    return SimpleNamespace(
        id=10,
        external_id="gamerpower:42",
        title="Example",
        url="https://example.com",
        store="Steam",
        kind=kind,
        starts_at=datetime(2026, 7, 1, tzinfo=UTC),
        ends_at=datetime(2026, 7, 22, tzinfo=UTC),
    )


def test_giveaway_fingerprint_is_stable_and_type_sensitive() -> None:
    first = GiveawayNotificationService.fingerprint(1, giveaway())
    assert first == GiveawayNotificationService.fingerprint(1, giveaway())
    assert first != GiveawayNotificationService.fingerprint(1, giveaway(GiveawayKind.WEEKEND))
    assert first != GiveawayNotificationService.fingerprint(2, giveaway())


def test_giveaway_cards_distinguish_permanent_and_temporary() -> None:
    permanent = GiveawayNotificationService.build_message(giveaway(), "en")
    temporary = GiveawayNotificationService.build_message(giveaway(GiveawayKind.WEEKEND), "en")
    assert "remains in your library" in permanent
    assert "Access ends" in temporary
    assert "None" not in permanent + temporary


def test_free_general_toggle_and_premium_type_filters() -> None:
    item = giveaway(GiveawayKind.WEEKEND)
    disabled = SimpleNamespace(giveaway_notifications_enabled=False, is_premium=False)
    free = SimpleNamespace(giveaway_notifications_enabled=True, is_premium=False)
    premium_keep = SimpleNamespace(
        giveaway_notifications_enabled=True,
        is_premium=True,
        giveaway_notification_kinds=["keep"],
    )
    premium_weekend = SimpleNamespace(
        giveaway_notifications_enabled=True,
        is_premium=True,
        giveaway_notification_kinds=["weekend"],
    )
    expired = SimpleNamespace(giveaway_notifications_enabled=True, is_premium=False)
    assert not GiveawayNotificationService.accepts(disabled, item)
    assert GiveawayNotificationService.accepts(free, item)
    assert not GiveawayNotificationService.accepts(premium_keep, item)
    assert GiveawayNotificationService.accepts(premium_weekend, item)
    assert GiveawayNotificationService.accepts(expired, item)
