from datetime import UTC, datetime
from decimal import Decimal

import pytest

from steam_radar.bot.keyboards import notification_keyboard
from steam_radar.services.monitor import PriceMonitor
from steam_radar.services.notifications import CAPTION_LIMIT, NotificationData, build_game_notification


def payload(premium: bool, prices: list[Decimal] | None = None) -> NotificationData:
    return NotificationData(
        app_id=252490,
        name="Rust & Friends",
        image_url="https://cdn.example/header.jpg",
        region_name="Polska",
        currency="PLN",
        base_price=Decimal("129.00"),
        current_price=Decimal("64.50"),
        discount_percent=50,
        previous_price=Decimal("100"),
        observed_prices=prices or [Decimal("129"), Decimal("64.50")],
        reasons=["discount", "new_low"],
        updated_at=datetime(2026, 7, 19, 14, 30, tzinfo=UTC),
        premium=premium,
    )


@pytest.mark.parametrize("language", ["ru", "en", "uk", "pl"])
def test_notification_localized_linked_and_without_none(language: str) -> None:
    content = build_game_notification(payload(False), language)
    assert 'href="https://store.steampowered.com/app/252490"' in content
    assert "Rust &amp; Friends" in content
    assert "64.50 PLN" in content
    assert "None" not in content and "null" not in content
    assert len(content) <= CAPTION_LIMIT


def test_premium_has_analysis_but_single_observation_does_not_invent_score() -> None:
    content = build_game_notification(payload(True), "en")
    assert "Premium analysis" in content and "/100" in content
    single = build_game_notification(payload(True, [Decimal("64.50")]), "en")
    assert "not enough data" in single
    assert "/100" not in single


def test_confirmed_steam_historical_low_is_labelled_separately() -> None:
    data = payload(True)
    data = NotificationData(
        **{name: getattr(data, name) for name in data.__dataclass_fields__ if name != "steam_historical_low"},
        steam_historical_low=Decimal("49.99"),
    )
    content = build_game_notification(data, "en")
    assert "Steam historical low" in content
    assert "49.99 PLN" in content


def test_saving_and_observed_minimum_wording_are_honest() -> None:
    content = build_game_notification(payload(True), "en")
    assert "64.50 PLN" in content
    assert "Low since tracking started" in content
    assert "Historical minimum" not in content


def test_premium_keyboard_has_analysis_and_correct_steam_url() -> None:
    keyboard = notification_keyboard("en", 252490, rule_id=7, premium=True)
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert any(button.url == "https://store.steampowered.com/app/252490" for button in buttons)
    assert any(button.callback_data == "premium_analytics:7" for button in buttons)
    assert any(button.callback_data == "ui:close" for button in buttons)


@pytest.mark.asyncio
async def test_photo_is_used_and_broken_image_falls_back_to_text() -> None:
    class Bot:
        def __init__(self, fail_photo: bool = False) -> None:
            self.fail_photo = fail_photo
            self.photos = []
            self.messages = []

        async def send_photo(self, *args, **kwargs):
            self.photos.append((args, kwargs))
            if self.fail_photo:
                raise RuntimeError("bad image")

        async def send_message(self, *args, **kwargs):
            self.messages.append((args, kwargs))

    monitor = object.__new__(PriceMonitor)
    monitor.bot = Bot()
    await monitor._send_notification(1, "caption", None, "https://cdn/image.jpg", 10)
    assert len(monitor.bot.photos) == 1 and not monitor.bot.messages
    monitor.bot = Bot(fail_photo=True)
    await monitor._send_notification(1, "caption", None, "https://cdn/broken.jpg", 10)
    assert len(monitor.bot.photos) == 1 and len(monitor.bot.messages) == 1
