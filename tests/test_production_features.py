from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from steam_radar.bot.handlers import _premium_screen
from steam_radar.config import Settings
from steam_radar.db.models import Plan
from steam_radar.services.digest import DigestService
from steam_radar.services.monitor import PriceMonitor


def test_production_rejects_test_payments() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="production", telegram_payment_test_mode=True, bot_token="1:test")


def test_development_allows_test_payments() -> None:
    assert Settings(app_env="development", telegram_payment_test_mode=True).telegram_payment_test_mode


def test_active_premium_screen_contains_dates_and_limit() -> None:
    now = datetime.now(UTC)
    user = SimpleNamespace(
        plan=Plan.PREMIUM,
        premium_until=now + timedelta(days=30),
        premium_started_at=now,
        created_at=now,
        is_premium=True,
    )
    content, keyboard = _premium_screen(user, "en", True)
    assert "Subscription active" in content
    assert "0 of 100 games" in content
    assert any(button.callback_data == "premium:history" for row in keyboard.inline_keyboard for button in row)


def test_free_premium_screen_marks_test_mode() -> None:
    content, _ = _premium_screen(None, "en", True)
    assert "Stars will not be charged" in content


@pytest.mark.parametrize(("value", "expected"), [(time(23, 30), True), (time(7, 59), True), (time(12), False)])
def test_quiet_hours_across_midnight(value: time, expected: bool) -> None:
    assert PriceMonitor.is_quiet(value, time(23), time(8)) is expected


def test_notification_fingerprint_changes_with_price_discount_and_currency() -> None:
    first = PriceMonitor._fingerprint(1, "target", Decimal("100"), 50, "KZT")
    assert first == PriceMonitor._fingerprint(1, "target", Decimal("100"), 50, "KZT")
    assert first != PriceMonitor._fingerprint(1, "target", Decimal("99"), 50, "KZT")
    assert first != PriceMonitor._fingerprint(1, "target", Decimal("100"), 60, "KZT")
    assert first != PriceMonitor._fingerprint(1, "target", Decimal("100"), 50, "RUB")


def test_premium_notification_conditions_are_combined() -> None:
    rule = SimpleNamespace(
        max_price=Decimal("100"),
        target_currency="PLN",
        min_discount=40,
        historical_low_only=False,
        user=SimpleNamespace(is_premium=True),
        notify_on_new_historical_low=True,
        notify_on_known_historical_low=False,
        notify_on_any_price_drop=True,
        minimum_price_drop_amount=Decimal("20"),
        minimum_price_drop_percent=10,
    )
    price = SimpleNamespace(final=Decimal("80"), currency="PLN", discount_percent=50)
    previous = SimpleNamespace(final_price=Decimal("120"), currency="PLN")
    reasons = PriceMonitor._notification_reasons(rule, price, previous, Decimal("90"), True, True)
    assert reasons == ["target", "discount", "new_low", "any_drop", "drop_amount", "drop_percent"]


def test_notification_repeat_policies() -> None:
    rule = SimpleNamespace(
        repeat_notification_policy="on_change",
        last_notification_fingerprint="same",
        last_notified_at=datetime.now(UTC),
        condition_was_met=True,
    )
    assert PriceMonitor._is_duplicate(rule, "same", True)
    assert not PriceMonitor._is_duplicate(rule, "changed", True)
    rule.repeat_notification_policy = "reentry"
    assert PriceMonitor._is_duplicate(rule, "changed", True)
    rule.repeat_notification_policy = "once"
    assert PriceMonitor._is_duplicate(rule, "changed", True)


@pytest.mark.parametrize("language", ["ru", "en", "uk", "pl"])
def test_notification_card_is_localized_and_omits_missing_optional_values(language: str) -> None:
    rule = SimpleNamespace(
        game=SimpleNamespace(name="Rust"),
        user=SimpleNamespace(is_premium=False, country_code="PL"),
    )
    price = SimpleNamespace(initial=Decimal("100"), final=Decimal("80"), currency="PLN", discount_percent=20)
    content = PriceMonitor._notification_content(rule, price, ["discount"], None, None, None, language)
    assert "Rust" in content
    assert "20%" in content
    assert "None" not in content


def test_daily_and_weekly_digest_due() -> None:
    now = datetime(2026, 7, 20, 9, 5, tzinfo=UTC)  # Monday
    daily = SimpleNamespace(
        timezone="UTC", daily_digest_time=time(9), daily_digest_enabled=True, daily_digest_last_at=None
    )
    weekly = SimpleNamespace(
        timezone="UTC",
        weekly_digest_time=time(9),
        weekly_digest_enabled=True,
        weekly_digest_weekday=0,
        weekly_digest_last_at=None,
    )
    assert DigestService._due(daily, now)
    assert DigestService._due(weekly, now, "weekly")
    weekly.digest_weekday = 1
    assert not DigestService._due(weekly, now)
