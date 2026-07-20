import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage

from steam_radar.bot.admin import admin_keyboard, deals_broadcast_preview, deals_broadcast_start, is_admin
from steam_radar.config import Settings
from steam_radar.db.models import DealBroadcastRecipient, DealBroadcastRun
from steam_radar.services.deal_broadcast import DealBroadcastService, DealItem


def _item(
    name: str = "Rust",
    app_id: int = 252490,
    discount: int = 50,
    final: str = "50",
    *,
    new_low: bool = False,
    target: bool = False,
) -> DealItem:
    return DealItem(
        name=name,
        app_id=app_id,
        initial=Decimal("100"),
        final=Decimal(final),
        discount=discount,
        currency="PLN",
        checked_at=datetime.now(UTC),
        new_low=new_low,
        target_reached=target,
    )


def _user(language: str = "ru", premium: bool = True):
    return SimpleNamespace(
        language_code=language,
        timezone="Europe/Warsaw",
        country_code="PL",
        is_premium=premium,
        telegram_id=123,
        id=1,
    )


def test_admin_deal_broadcast_button_and_permissions() -> None:
    settings = Settings(admin_ids=frozenset({123}))
    assert is_admin(123, settings)
    assert not is_admin(999, settings)
    callbacks = [button.callback_data for row in admin_keyboard("en").inline_keyboard for button in row]
    assert "admin:broadcasts" in callbacks
    assert "await _deny" in inspect.getsource(deals_broadcast_preview)
    assert "await _deny" in inspect.getsource(deals_broadcast_start)
    assert "admin:deals_broadcast_confirm" in inspect.getsource(deals_broadcast_preview)


def test_database_constraints_prevent_parallel_run_and_duplicate_recipient() -> None:
    active_index = next(
        index for index in DealBroadcastRun.__table__.indexes if index.name == "uq_active_deal_broadcast"
    )
    assert active_index.unique
    constraints = {constraint.name for constraint in DealBroadcastRecipient.__table__.constraints}
    assert "uq_deal_broadcast_run_user" in constraints


def test_deals_are_sorted_by_low_target_discount_and_price() -> None:
    deals = [
        _item("cheap", 1, 10, "10"),
        _item("discount", 2, 80, "20"),
        _item("target", 3, 20, "30", target=True),
        _item("low", 4, 15, "40", new_low=True),
    ]
    assert [item.name for item in DealBroadcastService.sort_deals(deals)] == [
        "low",
        "target",
        "discount",
        "cheap",
    ]


@pytest.mark.parametrize("language", ["ru", "en", "uk", "pl"])
def test_personal_deal_pages_are_localized_and_safe(language: str) -> None:
    pages = DealBroadcastService.build_pages(_user(language), [_item("A&B <Game>", new_low=True, target=True)])
    assert len(pages) == 1
    assert "https://store.steampowered.com/app/252490" in pages[0]
    assert "A&amp;B &lt;Game&gt;" in pages[0]
    assert "None" not in pages[0]


def test_user_without_deals_gets_no_pages() -> None:
    assert DealBroadcastService.build_pages(_user(), []) == []


def test_completed_recipient_is_not_processed_twice() -> None:
    for status in ("sent", "deferred", "no_deals", "blocked"):
        assert DealBroadcastService.recipient_is_complete(status)
    assert not DealBroadcastService.recipient_is_complete("pending")
    assert not DealBroadcastService.recipient_is_complete("temporary_error")


def test_long_deal_list_is_split_only_between_games() -> None:
    deals = [_item(name=f"Game {index} " + "x" * 120, app_id=1000 + index) for index in range(60)]
    pages = DealBroadcastService.build_pages(_user(), deals, limit=1200)
    assert len(pages) > 1
    assert all(len(page) < 1400 for page in pages)
    assert sum(page.count("store.steampowered.com/app/") for page in pages) == len(deals)


@pytest.mark.asyncio
async def test_retry_after_is_retried(monkeypatch) -> None:
    calls = 0

    class Bot:
        async def send_message(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TelegramRetryAfter(SendMessage(chat_id=123, text="x"), "retry", 0)

    async def no_sleep(_):
        return None

    monkeypatch.setattr("steam_radar.services.deal_broadcast.asyncio.sleep", no_sleep)
    service = DealBroadcastService(Bot(), None, None, Settings())
    assert await service._send_page(_user(), "hello") == "sent"
    assert calls == 2


@pytest.mark.asyncio
async def test_blocked_user_is_classified_without_retry() -> None:
    calls = 0

    class Bot:
        async def send_message(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            raise TelegramForbiddenError(SendMessage(chat_id=123, text="x"), "blocked")

    service = DealBroadcastService(Bot(), None, None, Settings())
    assert await service._send_page(_user(), "hello") == "blocked"
    assert calls == 1


def test_stale_threshold_covers_daily_free_refresh() -> None:
    settings = Settings(deal_broadcast_price_max_age_hours=26)
    assert datetime.now(UTC) - timedelta(hours=settings.deal_broadcast_price_max_age_hours) < datetime.now(UTC)


def test_only_fresh_valid_discount_is_selected() -> None:
    cutoff = datetime.now(UTC) - timedelta(hours=26)
    valid = SimpleNamespace(
        checked_at=datetime.now(UTC),
        discount_percent=50,
        initial_price=Decimal("100"),
        final_price=Decimal("50"),
        currency="PLN",
    )
    assert DealBroadcastService.is_current_deal(valid, cutoff)
    valid.discount_percent = 0
    assert not DealBroadcastService.is_current_deal(valid, cutoff)
    valid.discount_percent = 50
    valid.checked_at = cutoff - timedelta(seconds=1)
    assert not DealBroadcastService.is_current_deal(valid, cutoff)
