import asyncio
import hashlib
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from aiogram import Bot
from redis.exceptions import LockError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from steam_radar.bot.keyboards import deal_broadcast_keyboard, notification_keyboard
from steam_radar.config import Settings
from steam_radar.constants import REGIONS
from steam_radar.db.models import DeferredNotification, NotificationLog, PriceSnapshot, SystemError, User, WatchRule
from steam_radar.i18n import text
from steam_radar.services.pricing import format_money
from steam_radar.services.steam import SteamProvider

log = structlog.get_logger()


class PriceMonitor:
    def __init__(self, bot: Bot, session_factory: async_sessionmaker, steam: SteamProvider, settings: Settings) -> None:
        self.bot = bot
        self.session_factory = session_factory
        self.steam = steam
        self.settings = settings

    async def run(self, force: bool = False) -> int:
        lock = self.steam.redis.lock("lock:sync:prices", timeout=1800, blocking_timeout=0)
        if not await lock.acquire():
            log.info("price_sync_skipped", reason="already_running")
            return 0
        now = datetime.now(UTC)
        processed = 0
        try:
            async with self.session_factory() as session:
                rules = (
                    await session.scalars(
                        select(WatchRule)
                        .options(selectinload(WatchRule.user), selectinload(WatchRule.game))
                        .where(WatchRule.enabled.is_(True))
                        .order_by(WatchRule.last_checked_at.asc().nullsfirst())
                        .limit(self.settings.monitor_batch_size)
                    )
                ).all()
            checked: dict[tuple[int, str], object] = {}
            for rule in rules:
                hours = self.settings.premium_check_hours if rule.user.is_premium else self.settings.free_check_hours
                if not force and rule.last_checked_at and rule.last_checked_at > now - timedelta(hours=hours):
                    continue
                key = (rule.game.steam_app_id, rule.user.country_code)
                try:
                    if key not in checked:
                        checked[key] = await self._fetch_with_retry(
                            rule.game.steam_app_id, rule.user.country_code, force
                        )
                        await asyncio.sleep(self.settings.steam_request_delay)
                    _, price = checked[key]
                    await self._process(rule.id, price, now)
                    processed += 1
                except Exception as error:  # one broken game must not stop the monitoring batch
                    log.warning("price_check_failed", rule_id=rule.id, error=str(error))
                    await self._record_error("price_monitor", error)
            return processed
        finally:
            try:
                await lock.release()
            except LockError:
                pass

    async def _fetch_with_retry(self, app_id: int, country: str, force: bool):
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                region = REGIONS.get(country)
                if region is None:
                    raise ValueError(f"Unsupported Steam region: {country}")
                return await self.steam.details(app_id, region.steam_country_code, force_refresh=force)
            except Exception as error:
                last_error = error
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
        raise last_error or RuntimeError("Steam request failed")

    async def _process(self, rule_id: int, price, now: datetime) -> None:
        async with self.session_factory() as session:
            rule = await session.scalar(
                select(WatchRule)
                .options(selectinload(WatchRule.user), selectinload(WatchRule.game))
                .where(WatchRule.id == rule_id)
            )
            rule.last_checked_at = now
            if price is None:
                await session.commit()
                return
            latest = await session.scalar(
                select(PriceSnapshot)
                .where(
                    PriceSnapshot.game_id == rule.game_id,
                    PriceSnapshot.country_code == rule.user.country_code,
                )
                .order_by(PriceSnapshot.checked_at.desc())
                .limit(1)
            )
            if self._price_changed(latest, price):
                session.add(
                    PriceSnapshot(
                        game_id=rule.game_id,
                        country_code=rule.user.country_code,
                        currency=price.currency,
                        initial_price=price.initial,
                        final_price=price.final,
                        discount_percent=price.discount_percent,
                        checked_at=now,
                    )
                )
            historical_low = await session.scalar(
                select(func.min(PriceSnapshot.final_price)).where(
                    PriceSnapshot.game_id == rule.game_id,
                    PriceSnapshot.country_code == rule.user.country_code,
                    PriceSnapshot.currency == price.currency,
                    PriceSnapshot.checked_at < now,
                )
            )
            currency_matches = rule.max_price is None or rule.target_currency in {None, price.currency}
            matches = currency_matches and self._matches(rule, price.final, price.discount_percent, historical_low)
            new_low = historical_low is not None and price.final < historical_low
            reasons = self._notification_reasons(rule, price, latest, historical_low, matches, new_low)
            notification_type = "+".join(reasons) if reasons else None
            fingerprint = self._fingerprint(
                rule.id, notification_type, price.final, price.discount_percent, price.currency
            )
            duplicate = self._is_duplicate(rule, fingerprint, bool(reasons))
            if not reasons:
                rule.condition_was_met = False
                if rule.repeat_notification_policy != "once":
                    rule.last_notification_fingerprint = None
            if notification_type and not duplicate and rule.user.notifications_enabled and rule.notifications_enabled:
                language = rule.user.language_code
                observed_range = await session.execute(
                    select(func.min(PriceSnapshot.final_price), func.max(PriceSnapshot.final_price)).where(
                        PriceSnapshot.game_id == rule.game_id,
                        PriceSnapshot.country_code == rule.user.country_code,
                        PriceSnapshot.currency == price.currency,
                    )
                )
                minimum, maximum = observed_range.one()
                content = self._notification_content(rule, price, reasons, historical_low, minimum, maximum, language)
                markup = notification_keyboard(language, rule.game.steam_app_id)
                send_after = self._quiet_until(rule.user, now)
                if send_after:
                    session.add(
                        DeferredNotification(
                            user_id=rule.user_id,
                            game_id=rule.game_id,
                            steam_app_id=rule.game.steam_app_id,
                            fingerprint=fingerprint,
                            notification_type=notification_type,
                            content=content,
                            created_at=now,
                            send_after=send_after,
                        )
                    )
                else:
                    await self.bot.send_message(rule.user.telegram_id, content, parse_mode="HTML", reply_markup=markup)
                rule.last_notified_price = price.final
                rule.last_notified_discount = price.discount_percent
                rule.last_notification_type = notification_type
                rule.last_notification_fingerprint = fingerprint
                rule.last_notified_at = now
                rule.condition_was_met = True
                session.add(
                    NotificationLog(
                        user_id=rule.user_id,
                        game_id=rule.game_id,
                        price=price.final,
                        discount_percent=price.discount_percent,
                        sent_at=now,
                    )
                )
            await session.commit()

    async def send_deferred(self) -> int:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(DeferredNotification, User.telegram_id, User.language_code)
                        .join(User, User.id == DeferredNotification.user_id)
                        .where(DeferredNotification.sent_at.is_(None), DeferredNotification.send_after <= now)
                        .order_by(DeferredNotification.send_after)
                        .limit(self.settings.monitor_batch_size)
                    )
                ).all()
            )
            sent = 0
            for item, telegram_id, language in rows:
                try:
                    markup = (
                        deal_broadcast_keyboard(language)
                        if item.keyboard_type == "deal_broadcast"
                        else notification_keyboard(language, item.steam_app_id)
                        if item.steam_app_id
                        else None
                    )
                    await self.bot.send_message(
                        telegram_id,
                        item.content,
                        parse_mode="HTML",
                        reply_markup=markup,
                        disable_web_page_preview=True,
                    )
                    item.sent_at = now
                    sent += 1
                except Exception as error:
                    log.warning("deferred_notification_failed", notification_id=item.id, error=str(error))
            await session.commit()
        return sent

    @staticmethod
    def _price_changed(latest: PriceSnapshot | None, price) -> bool:
        return (
            latest is None
            or latest.initial_price != price.initial
            or latest.final_price != price.final
            or latest.discount_percent != price.discount_percent
            or latest.currency != price.currency
        )

    @staticmethod
    def _fingerprint(rule_id: int, kind: str | None, price: Decimal, discount: int, currency: str) -> str:
        raw = f"{rule_id}:{kind}:{currency}:{price}:{discount}".encode()
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _is_duplicate(rule: WatchRule, fingerprint: str, has_reasons: bool) -> bool:
        if not has_reasons:
            return False
        if rule.repeat_notification_policy == "once":
            return rule.last_notified_at is not None
        if rule.repeat_notification_policy == "reentry":
            return rule.condition_was_met
        return fingerprint == rule.last_notification_fingerprint

    @staticmethod
    def _notification_reasons(rule, price, previous, historical_low, matches: bool, new_low: bool) -> list[str]:
        reasons: list[str] = []
        basic_allowed = not rule.historical_low_only or historical_low is None or price.final <= historical_low
        if (
            basic_allowed
            and rule.max_price is not None
            and rule.target_currency in {None, price.currency}
            and price.final <= rule.max_price
        ):
            reasons.append("target")
        if basic_allowed and rule.min_discount is not None and price.discount_percent >= rule.min_discount:
            reasons.append("discount")
        if rule.user.is_premium:
            if new_low and rule.notify_on_new_historical_low:
                reasons.append("new_low")
            elif historical_low is not None and price.final == historical_low and rule.notify_on_known_historical_low:
                reasons.append("known_low")
            if previous is not None and previous.currency == price.currency and price.final < previous.final_price:
                drop = previous.final_price - price.final
                percent = Decimal(0) if previous.final_price == 0 else drop / previous.final_price * 100
                if rule.notify_on_any_price_drop:
                    reasons.append("any_drop")
                if rule.minimum_price_drop_amount is not None and drop >= rule.minimum_price_drop_amount:
                    reasons.append("drop_amount")
                if rule.minimum_price_drop_percent is not None and percent >= rule.minimum_price_drop_percent:
                    reasons.append("drop_percent")
        if matches and not reasons:
            reasons.append("rule")
        return list(dict.fromkeys(reasons))

    @staticmethod
    def _notification_content(rule, price, reasons, historical_low, minimum, maximum, language: str) -> str:
        lines = [
            text(language, "notification_title"),
            f"\n🎮 <b>{escape(rule.game.name)}</b>",
            f"\n{text(language, 'notification_was')}\n<b>{format_money(price.initial, price.currency)}</b>",
            f"\n{text(language, 'notification_now')}\n<b>{format_money(price.final, price.currency)}</b>",
        ]
        if price.discount_percent:
            lines.append(f"\n{text(language, 'discount')}\n<b>−{price.discount_percent}%</b>")
        region = text(language, f"region_{rule.user.country_code.lower()}")
        lines.append(f"\n{text(language, 'region_label')}\n<b>{region}</b>")
        reason_keys = {
            "target": "reason_target",
            "discount": "reason_discount",
            "new_low": "reason_new_low",
            "known_low": "reason_known_low",
            "any_drop": "reason_any_drop",
            "drop_amount": "reason_drop_amount",
            "drop_percent": "reason_drop_percent",
            "rule": "reason_rule",
        }
        if reasons:
            lines.append("\n" + "\n".join(f"✅ {text(language, reason_keys[item])}" for item in reasons))
        if rule.user.is_premium and minimum is not None:
            lines.append(
                f"\n📉 {text(language, 'notification_observed_low')}: <b>{format_money(minimum, price.currency)}</b>"
            )
            difference = price.final - minimum
            if difference > 0:
                lines.append(
                    f"⭐ {text(language, 'notification_to_low')}: <b>{format_money(difference, price.currency)}</b>"
                )
            if maximum is not None and maximum > minimum:
                score = int(((maximum - price.final) / (maximum - minimum) * 100).quantize(Decimal("1")))
                lines.append(f"⭐ {text(language, 'notification_deal_score')}: <b>{max(0, min(100, score))}/100</b>")
        return "\n".join(lines)

    @staticmethod
    def is_quiet(local_time: time, start: time, end: time) -> bool:
        return start <= local_time < end if start < end else local_time >= start or local_time < end

    @classmethod
    def _quiet_until(cls, user, now: datetime) -> datetime | None:
        if not user.quiet_hours_enabled or not user.quiet_hours_start or not user.quiet_hours_end:
            return None
        try:
            zone = ZoneInfo(user.timezone)
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("UTC")
        local = now.astimezone(zone)
        if not cls.is_quiet(local.time().replace(tzinfo=None), user.quiet_hours_start, user.quiet_hours_end):
            return None
        end_date = local.date()
        if (
            user.quiet_hours_start >= user.quiet_hours_end
            and local.time().replace(tzinfo=None) >= user.quiet_hours_start
        ):
            end_date += timedelta(days=1)
        return datetime.combine(end_date, user.quiet_hours_end, tzinfo=zone).astimezone(UTC)

    @staticmethod
    def _matches(rule: WatchRule, price: Decimal, discount: int, historical_low: Decimal | None) -> bool:
        if rule.historical_low_only and historical_low is not None and price > historical_low:
            return False
        if rule.max_price is not None and price <= rule.max_price:
            return True
        return rule.min_discount is not None and discount >= rule.min_discount

    async def _record_error(self, component: str, error: Exception) -> None:
        async with self.session_factory() as session:
            session.add(SystemError(component=component, message=str(error)[:2000], occurred_at=datetime.now(UTC)))
            await session.commit()
