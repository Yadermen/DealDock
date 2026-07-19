import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html import escape
from time import monotonic
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from redis.asyncio import Redis
from redis.exceptions import LockError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from steam_radar.bot.keyboards import deal_broadcast_keyboard
from steam_radar.config import Settings
from steam_radar.db.models import (
    DealBroadcastRecipient,
    DealBroadcastRun,
    DeferredNotification,
    Game,
    PriceSnapshot,
    User,
    WatchRule,
)
from steam_radar.i18n import text
from steam_radar.services.monitor import PriceMonitor
from steam_radar.services.pricing import format_money

log = structlog.get_logger()


def _admin_close_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")]]
    )


@dataclass(frozen=True, slots=True)
class DealItem:
    name: str
    app_id: int
    initial: Decimal
    final: Decimal
    discount: int
    currency: str
    checked_at: datetime
    new_low: bool
    target_reached: bool


class DealBroadcastService:
    def __init__(
        self,
        bot: Bot,
        session_factory: async_sessionmaker,
        redis: Redis,
        settings: Settings,
    ) -> None:
        self.bot = bot
        self.session_factory = session_factory
        self.redis = redis
        self.settings = settings

    async def preview(self) -> dict[str, object]:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            active = await session.scalar(
                select(func.count()).select_from(User).where(User.last_seen_at >= now - timedelta(days=30))
            )
            users_with_games = await session.scalar(
                select(func.count(func.distinct(WatchRule.user_id))).where(WatchRule.enabled.is_(True))
            )
            games = await session.scalar(select(func.count()).select_from(WatchRule).where(WatchRule.enabled.is_(True)))
            updated = await session.scalar(select(func.max(PriceSnapshot.checked_at)))
        return {
            "active": active or 0,
            "users_with_games": users_with_games or 0,
            "games": games or 0,
            "updated": updated,
        }

    async def create_run(self, admin_id: int) -> DealBroadcastRun | None:
        run = DealBroadcastRun(
            id=str(uuid.uuid4()),
            admin_telegram_id=admin_id,
            status="pending",
            respect_quiet_hours=True,
            created_at=datetime.now(UTC),
        )
        async with self.session_factory() as session:
            session.add(run)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None
        return run

    async def resume_active(self) -> None:
        async with self.session_factory() as session:
            run_ids = list(
                await session.scalars(
                    select(DealBroadcastRun.id).where(DealBroadcastRun.status.in_(("pending", "running")))
                )
            )
        for run_id in run_ids:
            asyncio.create_task(self.run(run_id), name=f"deal-broadcast-resume-{run_id}")

    async def run(self, run_id: str) -> None:
        lock = self.redis.lock(f"lock:deal-broadcast:{run_id}", timeout=3600, blocking_timeout=0)
        if not await lock.acquire():
            return
        started = monotonic()
        try:
            async with self.session_factory() as session:
                run = await session.get(DealBroadcastRun, run_id)
                if run is None or run.admin_telegram_id not in self.settings.admin_ids:
                    return
                run.status = "running"
                run.started_at = run.started_at or datetime.now(UTC)
                await session.commit()
            last_user_id = 0
            while True:
                async with self.session_factory() as session:
                    user_ids = list(
                        await session.scalars(
                            select(User.id)
                            .join(WatchRule, WatchRule.user_id == User.id)
                            .where(User.id > last_user_id, WatchRule.enabled.is_(True))
                            .distinct()
                            .order_by(User.id)
                            .limit(self.settings.deal_broadcast_batch_size)
                        )
                    )
                if not user_ids:
                    break
                for user_id in user_ids:
                    await self._process_user(run_id, user_id)
                last_user_id = user_ids[-1]
            await self._finish(run_id, started)
        except Exception as error:
            log.exception("deal_broadcast_failed", run_id=run_id, exception_type=type(error).__name__)
            async with self.session_factory() as session:
                run = await session.get(DealBroadcastRun, run_id)
                if run:
                    run.status = "failed"
                    run.finished_at = datetime.now(UTC)
                    await session.commit()
        finally:
            try:
                await lock.release()
            except LockError:
                pass

    async def _process_user(self, run_id: str, user_id: int) -> None:
        async with self.session_factory() as session:
            run = await session.get(DealBroadcastRun, run_id)
            user = await session.get(User, user_id)
            if run is None or user is None:
                return
            recipient = await session.scalar(
                select(DealBroadcastRecipient).where(
                    DealBroadcastRecipient.run_id == run_id, DealBroadcastRecipient.user_id == user_id
                )
            )
            if recipient and self.recipient_is_complete(recipient.status):
                return
            if recipient is None:
                recipient = DealBroadcastRecipient(run_id=run_id, user_id=user_id, status="pending")
                session.add(recipient)
                await session.flush()
            if recipient.content_pages is None:
                deals, stale = await self._load_deals(session, user)
                pages = self.build_pages(user, deals)
                recipient.content_pages = pages
                recipient.game_count = len(deals)
                run.stale_games += stale
                run.games_included += len(deals)
                if not pages:
                    recipient.status = "no_deals"
                    recipient.processed_at = datetime.now(UTC)
                    run.no_deals += 1
                    run.users_checked += 1
                    await session.commit()
                    return
                send_after = PriceMonitor._quiet_until(user, datetime.now(UTC)) if run.respect_quiet_hours else None
                if send_after:
                    for page_index, content in enumerate(pages):
                        session.add(
                            DeferredNotification(
                                user_id=user.id,
                                game_id=None,
                                steam_app_id=None,
                                keyboard_type="deal_broadcast",
                                fingerprint=f"deal-broadcast:{run_id}:{user.id}:{page_index}",
                                notification_type="deal_broadcast",
                                content=content,
                                created_at=datetime.now(UTC),
                                send_after=send_after,
                            )
                        )
                    recipient.status = "deferred"
                    recipient.message_count = len(pages)
                    recipient.processed_at = datetime.now(UTC)
                    run.deferred += 1
                    run.users_checked += 1
                    await session.commit()
                    return
                await session.commit()
            pages = recipient.content_pages or []
            already_sent = recipient.message_count

        status = "sent"
        for page_index in range(already_sent, len(pages)):
            outcome = await self._send_page(user, pages[page_index])
            async with self.session_factory() as session:
                run = await session.get(DealBroadcastRun, run_id)
                recipient = await session.scalar(
                    select(DealBroadcastRecipient).where(
                        DealBroadcastRecipient.run_id == run_id,
                        DealBroadcastRecipient.user_id == user_id,
                    )
                )
                if outcome == "sent":
                    recipient.message_count = page_index + 1
                    run.messages_sent += 1
                    await session.commit()
                else:
                    status = outcome
                    recipient.status = outcome
                    recipient.processed_at = datetime.now(UTC)
                    run.blocked += int(outcome == "blocked")
                    run.temporary_errors += int(outcome == "temporary_error")
                    run.permanent_errors += int(outcome == "failed")
                    run.users_checked += 1
                    await session.commit()
                    return
        async with self.session_factory() as session:
            run = await session.get(DealBroadcastRun, run_id)
            recipient = await session.scalar(
                select(DealBroadcastRecipient).where(
                    DealBroadcastRecipient.run_id == run_id, DealBroadcastRecipient.user_id == user_id
                )
            )
            recipient.status = status
            recipient.processed_at = datetime.now(UTC)
            run.users_checked += 1
            await session.commit()

    async def _send_page(self, user: User, content: str) -> str:
        for attempt in range(3):
            try:
                await self.bot.send_message(
                    user.telegram_id,
                    content,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                    reply_markup=deal_broadcast_keyboard(user.language_code),
                )
                await asyncio.sleep(1 / self.settings.deal_broadcast_messages_per_second)
                return "sent"
            except TelegramRetryAfter as error:
                if attempt == 2:
                    return "temporary_error"
                await asyncio.sleep(min(float(error.retry_after), 30))
            except TelegramForbiddenError:
                return "blocked"
            except TelegramBadRequest as error:
                log.warning("deal_broadcast_bad_request", user_id=user.id, error=str(error))
                return "failed"
            except Exception as error:
                log.warning(
                    "deal_broadcast_send_retry",
                    user_id=user.id,
                    attempt=attempt + 1,
                    exception_type=type(error).__name__,
                )
                if attempt == 2:
                    return "temporary_error"
                await asyncio.sleep(2**attempt)
        return "temporary_error"

    async def _load_deals(self, session, user: User) -> tuple[list[DealItem], int]:
        cutoff = datetime.now(UTC) - timedelta(hours=self.settings.deal_broadcast_price_max_age_hours)
        active_rules = list(
            await session.scalars(select(WatchRule).where(WatchRule.user_id == user.id, WatchRule.enabled.is_(True)))
        )
        deals: list[DealItem] = []
        stale = 0
        for rule in active_rules:
            game = await session.get(Game, rule.game_id)
            snapshot = await session.scalar(
                select(PriceSnapshot)
                .where(
                    PriceSnapshot.game_id == rule.game_id,
                    PriceSnapshot.country_code == user.country_code,
                )
                .order_by(PriceSnapshot.checked_at.desc())
                .limit(1)
            )
            if snapshot is None or snapshot.checked_at < cutoff:
                stale += 1
                continue
            if not self.is_current_deal(snapshot, cutoff):
                continue
            previous_low = await session.scalar(
                select(func.min(PriceSnapshot.final_price)).where(
                    PriceSnapshot.game_id == rule.game_id,
                    PriceSnapshot.country_code == user.country_code,
                    PriceSnapshot.currency == snapshot.currency,
                    PriceSnapshot.checked_at < snapshot.checked_at,
                )
            )
            deals.append(
                DealItem(
                    name=game.name,
                    app_id=game.steam_app_id,
                    initial=snapshot.initial_price,
                    final=snapshot.final_price,
                    discount=snapshot.discount_percent,
                    currency=snapshot.currency,
                    checked_at=snapshot.checked_at,
                    new_low=previous_low is not None and snapshot.final_price < previous_low,
                    target_reached=(
                        rule.max_price is not None
                        and rule.target_currency in {None, snapshot.currency}
                        and snapshot.final_price <= rule.max_price
                    ),
                )
            )
        return self.sort_deals(deals), stale

    @staticmethod
    def sort_deals(deals: list[DealItem]) -> list[DealItem]:
        return sorted(deals, key=lambda item: (not item.new_low, not item.target_reached, -item.discount, item.final))

    @staticmethod
    def recipient_is_complete(status: str) -> bool:
        return status in {"sent", "deferred", "no_deals", "blocked"}

    @staticmethod
    def is_current_deal(snapshot, cutoff: datetime) -> bool:
        return bool(
            snapshot
            and snapshot.checked_at >= cutoff
            and snapshot.discount_percent > 0
            and snapshot.final_price >= 0
            and snapshot.initial_price > snapshot.final_price
            and snapshot.currency
        )

    @staticmethod
    def build_pages(user: User, deals: list[DealItem], limit: int = 3500) -> list[str]:
        if not deals:
            return []
        language = user.language_code
        units: list[str] = []
        for index, item in enumerate(deals, 1):
            markers = []
            if user.is_premium and item.new_low:
                markers.append(text(language, "deal_broadcast_new_low"))
            if user.is_premium and item.target_reached:
                markers.append(text(language, "deal_broadcast_target"))
            marker_text = "" if not markers else "\n   " + " · ".join(markers)
            units.append(
                f'{index}. 🎮 <a href="https://store.steampowered.com/app/{item.app_id}">{escape(item.name)}</a>\n'
                f"   <s>{format_money(item.initial, item.currency)}</s> → "
                f"<b>{format_money(item.final, item.currency)}</b>\n"
                f"   🔥 −{item.discount}%{marker_text}"
            )
        chunks: list[list[str]] = []
        current: list[str] = []
        for unit in units:
            if current and len("\n\n".join(current + [unit])) > limit - 500:
                chunks.append(current)
                current = []
            current.append(unit)
        if current:
            chunks.append(current)
        try:
            zone = ZoneInfo(user.timezone)
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("UTC")
        checked = max(item.checked_at for item in deals).astimezone(zone).strftime("%d.%m.%Y, %H:%M %Z")
        region = text(language, f"region_{user.country_code.lower()}")
        pages = []
        for page_index, chunk in enumerate(chunks, 1):
            page_label = (
                ""
                if len(chunks) == 1
                else "\n" + text(language, "deal_broadcast_page", current=page_index, total=len(chunks))
            )
            pages.append(
                text(language, "deal_broadcast_title", count=len(deals))
                + page_label
                + "\n\n"
                + "\n\n".join(chunk)
                + "\n\n"
                + text(language, "deal_broadcast_footer", checked=checked, region=region)
            )
        return pages

    async def _finish(self, run_id: str, started: float) -> None:
        async with self.session_factory() as session:
            run = await session.get(DealBroadcastRun, run_id)
            run.finished_at = datetime.now(UTC)
            run.status = "completed" if not run.temporary_errors and not run.permanent_errors else "partially_completed"
            await session.commit()
            admin_id = run.admin_telegram_id
            duration = max(0, int(monotonic() - started))
            report = {
                "run_id": run.id,
                "checked": run.users_checked,
                "sent": run.messages_sent,
                "no_deals": run.no_deals,
                "deferred": run.deferred,
                "blocked": run.blocked,
                "temporary": run.temporary_errors,
                "permanent": run.permanent_errors,
                "games": run.games_included,
                "stale": run.stale_games,
                "duration": f"{duration // 60}:{duration % 60:02d}",
            }
            admin = await session.scalar(select(User).where(User.telegram_id == admin_id))
            language = admin.language_code if admin else "ru"
        try:
            await self.bot.send_message(
                admin_id,
                text(language, "admin_deals_completed", **report),
                reply_markup=_admin_close_keyboard(language),
            )
        except Exception:
            log.exception("deal_broadcast_admin_report_failed", run_id=run_id, admin_id=admin_id)
        log.info("deal_broadcast_completed", admin_id=admin_id, **report)
