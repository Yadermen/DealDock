import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from redis.asyncio import Redis
from sqlalchemy import func, or_, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from steam_radar.bot.keyboards import broadcast_dismiss_keyboard
from steam_radar.bot.states import AdminBroadcast, AdminGameRefresh, AdminGiveaway, AdminPremium, AdminUserEdit
from steam_radar.config import Settings
from steam_radar.constants import COMPARISON_CURRENCIES, LANGUAGES, REGIONS, TIMEZONES
from steam_radar.db.models import (
    AdminUserAudit,
    Game,
    Giveaway,
    GiveawayKind,
    NotificationLog,
    Payment,
    Plan,
    PremiumAudit,
    PriceSnapshot,
    Referral,
    ReferralReward,
    SyncRun,
    SystemError,
    User,
    WatchRule,
)
from steam_radar.i18n import text
from steam_radar.services.condition_currency import convert_user_money_conditions
from steam_radar.services.currency import CurrencyError, CurrencyService
from steam_radar.services.deal_broadcast import DealBroadcastService
from steam_radar.services.steam import SteamError, SteamProvider
from steam_radar.services.sync import SyncCoordinator
from steam_radar.services.timezones import normalize_utc_offset

router = Router(name="admin")
log = structlog.get_logger()


def is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_ids


def admin_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=text(language, "admin_section_users"), callback_data="admin:users"),
                InlineKeyboardButton(text=text(language, "admin_section_premium"), callback_data="admin:premium"),
            ],
            [
                InlineKeyboardButton(text=text(language, "admin_section_broadcasts"), callback_data="admin:broadcasts"),
                InlineKeyboardButton(text=text(language, "admin_section_giveaways"), callback_data="admin:giveaways"),
            ],
            [
                InlineKeyboardButton(text=text(language, "admin_section_sync"), callback_data="admin:sync_section"),
                InlineKeyboardButton(text=text(language, "admin_section_errors"), callback_data="admin:errors"),
            ],
            [
                InlineKeyboardButton(text=text(language, "admin_section_games"), callback_data="admin_games:0"),
                InlineKeyboardButton(
                    text=text(language, "admin_section_transactions"),
                    callback_data="admin_transactions:0",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=text(language, "admin_section_referrals"),
                    callback_data="admin:referrals",
                )
            ],
            [InlineKeyboardButton(text=text(language, "admin_section_system"), callback_data="admin:system")],
            [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
        ]
    )


def admin_section_keyboard(language: str, section: str) -> InlineKeyboardMarkup:
    actions = {
        "broadcasts": [
            ("admin_broadcast", "admin:broadcast"),
            ("admin_deals_broadcast", "admin:deals_broadcast"),
        ],
        "giveaways": [
            ("admin_add_giveaway", "admin:add_giveaway"),
            ("admin_sync", "admin:sync"),
        ],
        "sync": [
            ("admin_sync", "admin:sync"),
            ("admin_refresh_game", "admin:refresh_game"),
        ],
        "system": [("admin_refresh", "admin:panel")],
    }[section]
    rows = [[InlineKeyboardButton(text=text(language, key), callback_data=callback)] for key, callback in actions]
    rows.extend(
        [
            [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="admin:panel")],
            [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_premium_root_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text(language, "admin_premium_choose"), callback_data="admin_premium_users:0")],
            [InlineKeyboardButton(text=text(language, "admin_premium_find"), callback_data="admin:premium_search")],
            [InlineKeyboardButton(text=text(language, "admin_premium_active"), callback_data="admin_premium_active:0")],
            [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="admin:panel")],
            [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
        ]
    )


def admin_close_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")]]
    )


def admin_back_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="admin:panel")],
            [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
        ]
    )


@router.callback_query(F.data == "admin:close")
async def admin_close(callback: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    if await _deny(callback, settings):
        return
    await state.clear()
    await callback.answer()
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass


async def _deny(event: Message | CallbackQuery, settings: Settings) -> bool:
    if is_admin(event.from_user.id, settings):
        return False
    if isinstance(event, CallbackQuery):
        await event.answer(text("ru", "access_denied"), show_alert=True)
    return True


@router.message(Command("admin"))
async def admin_command(
    message: Message, settings: Settings, session_factory: async_sessionmaker, redis: Redis
) -> None:
    log.info("admin_command_received", telegram_id=message.from_user.id)
    if await _deny(message, settings):
        log.warning("admin_command_denied", telegram_id=message.from_user.id)
        return
    language = await _admin_language(session_factory, message.from_user.id)
    try:
        await message.bot.send_message(
            message.chat.id,
            await _panel_text(session_factory, redis, language),
            reply_markup=admin_keyboard(language),
        )
    except Exception:
        log.exception("admin_panel_send_failed", telegram_id=message.from_user.id)
        raise


@router.callback_query(F.data == "admin:panel")
async def admin_panel(
    callback: CallbackQuery, settings: Settings, session_factory: async_sessionmaker, redis: Redis
) -> None:
    if await _deny(callback, settings):
        return
    language = await _admin_language(session_factory, callback.from_user.id)
    await callback.message.edit_text(
        await _panel_text(session_factory, redis, language), reply_markup=admin_keyboard(language)
    )
    await callback.answer()


@router.callback_query(F.data.in_({"admin:broadcasts", "admin:giveaways", "admin:sync_section", "admin:system"}))
async def admin_section(
    callback: CallbackQuery, settings: Settings, session_factory: async_sessionmaker, redis: Redis
) -> None:
    if await _deny(callback, settings):
        return
    language = await _admin_language(session_factory, callback.from_user.id)
    section = callback.data.split(":", 1)[1].removesuffix("_section")
    content = text(language, f"admin_{section}_title")
    if section == "system":
        content += "\n\n" + await _panel_text(session_factory, redis, language)
    await callback.message.edit_text(content, reply_markup=admin_section_keyboard(language, section))
    await callback.answer()


def _admin_time_boundaries() -> tuple[datetime, datetime]:
    now_aware = datetime.now(UTC)
    return now_aware, now_aware.replace(tzinfo=None)


async def _panel_text(session_factory: async_sessionmaker, redis: Redis, language: str = "ru") -> str:
    now_aware, now_naive = _admin_time_boundaries()
    async with session_factory() as session:
        users = await session.scalar(select(func.count()).select_from(User))
        new_day = await session.scalar(
            select(func.count()).select_from(User).where(User.created_at >= now_naive - timedelta(days=1))
        )
        new_week = await session.scalar(
            select(func.count()).select_from(User).where(User.created_at >= now_naive - timedelta(days=7))
        )
        new_month = await session.scalar(
            select(func.count()).select_from(User).where(User.created_at >= now_naive - timedelta(days=30))
        )
        active_day = await session.scalar(
            select(func.count()).select_from(User).where(User.last_seen_at >= now_aware - timedelta(days=1))
        )
        active_week = await session.scalar(
            select(func.count()).select_from(User).where(User.last_seen_at >= now_aware - timedelta(days=7))
        )
        active = await session.scalar(
            select(func.count()).select_from(User).where(User.last_seen_at >= now_aware - timedelta(days=30))
        )
        rules = await session.scalar(select(func.count()).select_from(WatchRule).where(WatchRule.enabled.is_(True)))
        games = await session.scalar(select(func.count()).select_from(Game))
        giveaways = await session.scalar(
            select(func.count()).select_from(Giveaway).where(Giveaway.active.is_(True), Giveaway.approved.is_(True))
        )
        premium = await session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.plan == Plan.PREMIUM,
                User.premium_until > now_aware,
            )
        )
        notifications = await session.scalar(select(func.count()).select_from(NotificationLog))
        sales = await session.scalar(select(func.count()).select_from(Payment).where(Payment.status == "successful"))
        stars = await session.scalar(
            select(func.coalesce(func.sum(Payment.amount_stars), 0)).where(Payment.status == "successful")
        )
        language_rows = (
            await session.execute(
                select(User.language_code, func.count()).group_by(User.language_code).order_by(func.count().desc())
            )
        ).all()
        region_rows = (
            await session.execute(
                select(User.country_code, func.count())
                .group_by(User.country_code)
                .order_by(func.count().desc())
                .limit(5)
            )
        ).all()
        timezone_rows = (
            await session.execute(
                select(User.timezone, func.count()).group_by(User.timezone).order_by(func.count().desc()).limit(5)
            )
        ).all()
        last_sync = await session.scalar(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(1))
        try:
            await session.execute(sql_text("SELECT 1"))
            postgres_status = text(language, "status_ok")
        except Exception:
            postgres_status = text(language, "status_error")
    try:
        redis_status = text(language, "status_ok") if await redis.ping() else text(language, "status_error")
    except Exception:
        redis_status = text(language, "status_error")
    sync_label = text(language, "never")
    if last_sync:
        sync_label = f"{last_sync.status} · {last_sync.started_at:%d.%m.%Y %H:%M} UTC"
    return (
        text(language, "admin_title")
        + "\n\n"
        + text(
            language,
            "admin_stats_detailed",
            users=users,
            new_day=new_day,
            new_week=new_week,
            new_month=new_month,
            active_day=active_day,
            active_week=active_week,
            active=active,
            rules=rules,
            premium=premium,
            free=max(0, users - premium),
            games=games,
            giveaways=giveaways,
            notifications=notifications,
            sales=sales,
            stars=stars,
            languages=", ".join(f"{code or '—'}: {count}" for code, count in language_rows) or "—",
            regions=", ".join(f"{code or '—'}: {count}" for code, count in region_rows) or "—",
            timezones=", ".join(f"{zone or '—'}: {count}" for zone, count in timezone_rows) or "—",
            sync=sync_label,
            postgres=postgres_status,
            redis=redis_status,
        )
    )


def _admin_page_keyboard(language: str, prefix: str, page: int, has_next: bool) -> InlineKeyboardMarkup:
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}:{page - 1}"))
    if has_next:
        navigation.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}:{page + 1}"))
    rows = [navigation] if navigation else []
    rows.extend(
        [
            [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="admin:panel")],
            [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("admin_games:"))
async def admin_games(
    callback: CallbackQuery,
    settings: Settings,
    session_factory: async_sessionmaker,
) -> None:
    if await _deny(callback, settings):
        return
    page = max(0, int(callback.data.split(":", 1)[1]))
    page_size = 15
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Game, func.count(WatchRule.id))
                .outerjoin(WatchRule, WatchRule.game_id == Game.id)
                .group_by(Game.id)
                .order_by(func.count(WatchRule.id).desc(), Game.name)
                .offset(page * page_size)
                .limit(page_size + 1)
            )
        ).all()
    language = await _admin_language(session_factory, callback.from_user.id)
    has_next = len(rows) > page_size
    items = rows[:page_size]
    content = text(
        language,
        "admin_games_list",
        page=page + 1,
        items="\n".join(f"• {game.name} · {game.steam_app_id} · 👥 {followers}" for game, followers in items)
        or text(language, "admin_empty"),
    )
    await callback.message.edit_text(
        content,
        reply_markup=_admin_page_keyboard(language, "admin_games", page, has_next),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_transactions:"))
async def admin_transactions(
    callback: CallbackQuery,
    settings: Settings,
    session_factory: async_sessionmaker,
) -> None:
    if await _deny(callback, settings):
        return
    page = max(0, int(callback.data.split(":", 1)[1]))
    page_size = 15
    async with session_factory() as session:
        payments = list(
            (
                await session.scalars(
                    select(Payment).order_by(Payment.paid_at.desc()).offset(page * page_size).limit(page_size + 1)
                )
            ).all()
        )
    language = await _admin_language(session_factory, callback.from_user.id)
    has_next = len(payments) > page_size
    items = payments[:page_size]
    content = text(
        language,
        "admin_transactions_list",
        page=page + 1,
        items="\n".join(
            f"• #{item.id} · {item.paid_at:%d.%m.%Y} · {item.amount_stars} ⭐ · {item.tariff} · {item.status}"
            for item in items
        )
        or text(language, "admin_empty"),
    )
    await callback.message.edit_text(
        content,
        reply_markup=_admin_page_keyboard(language, "admin_transactions", page, has_next),
    )
    await callback.answer()


async def _admin_language(session_factory: async_sessionmaker, telegram_id: int) -> str:
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    return user.language_code if user and user.language_code else "ru"


@router.callback_query(F.data == "admin:sync")
async def run_sync(callback: CallbackQuery, settings: Settings, sync: SyncCoordinator) -> None:
    if await _deny(callback, settings):
        return
    language = await _admin_language(sync.session_factory, callback.from_user.id)
    asyncio.create_task(sync.full_sync(), name=f"manual-sync-{callback.from_user.id}")
    await callback.answer(text(language, "admin_sync_started"), show_alert=True)


@router.callback_query(F.data == "admin:users")
@router.callback_query(F.data.startswith("admin_users:"))
async def recent_users(callback: CallbackQuery, settings: Settings, session_factory: async_sessionmaker) -> None:
    if await _deny(callback, settings):
        return
    page = int(callback.data.split(":", 1)[1]) if callback.data.startswith("admin_users:") else 0
    page_size = 12
    async with session_factory() as session:
        users = list(
            (
                await session.scalars(
                    select(User).order_by(User.created_at.desc()).offset(page * page_size).limit(page_size + 1)
                )
            ).all()
        )
    language = await _admin_language(session_factory, callback.from_user.id)
    has_next = len(users) > page_size
    users = users[:page_size]
    lines = [text(language, "admin_recent_users_title")] + [
        f"• {user.telegram_id} · @{user.username or '—'} · {user.created_at:%d.%m %H:%M}" for user in users
    ]
    rows = [
        [
            InlineKeyboardButton(
                text=f"@{user.username}" if user.username else str(user.telegram_id),
                callback_data=f"admin_premium_user:{user.id}",
            )
        ]
        for user in users
    ]
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin_users:{page - 1}"))
    if has_next:
        navigation.append(InlineKeyboardButton(text="➡️", callback_data=f"admin_users:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.extend(
        [
            [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="admin:panel")],
            [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
        ]
    )
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:referrals")
async def admin_referrals(
    callback: CallbackQuery,
    settings: Settings,
    session_factory: async_sessionmaker,
) -> None:
    if await _deny(callback, settings):
        return
    language = await _admin_language(session_factory, callback.from_user.id)
    async with session_factory() as session:
        total = int(await session.scalar(select(func.count()).select_from(Referral)) or 0)
        active = int(
            await session.scalar(select(func.count()).select_from(Referral).where(Referral.status == "active")) or 0
        )
        inviter_days = int(
            await session.scalar(
                select(func.coalesce(func.sum(ReferralReward.premium_days), 0)).where(
                    ReferralReward.reward_key != "invitee_activation"
                )
            )
            or 0
        )
        invitee_days = int(
            await session.scalar(
                select(func.coalesce(func.sum(ReferralReward.premium_days), 0)).where(
                    ReferralReward.reward_key == "invitee_activation"
                )
            )
            or 0
        )
        best = (
            await session.execute(
                select(User, func.count(Referral.id).label("amount"))
                .join(Referral, Referral.inviter_user_id == User.id)
                .where(Referral.status == "active")
                .group_by(User.id)
                .order_by(func.count(Referral.id).desc())
                .limit(1)
            )
        ).first()
    best_name = "—" if best is None else (f"@{best[0].username}" if best[0].username else str(best[0].telegram_id))
    await callback.message.edit_text(
        text(
            language,
            "admin_referrals_screen",
            total=total,
            active=active,
            pending=total - active,
            inviter_days=inviter_days,
            invitee_days=invitee_days,
            best=best_name,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text=text(language, "admin_referrers"), callback_data="admin_referrers:0"),
                    InlineKeyboardButton(
                        text=text(language, "admin_referral_search"),
                        callback_data="admin:premium_search",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=text(language, "admin_referral_rating"),
                        callback_data="admin_referrers:0",
                    ),
                    InlineKeyboardButton(
                        text=text(language, "admin_referral_recent"),
                        callback_data="admin_referrals_recent:0",
                    ),
                ],
                [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="admin:panel")],
                [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_referrers:"))
async def admin_referrers(
    callback: CallbackQuery,
    settings: Settings,
    session_factory: async_sessionmaker,
) -> None:
    if await _deny(callback, settings):
        return
    language = await _admin_language(session_factory, callback.from_user.id)
    page = max(0, int(callback.data.rsplit(":", 1)[1]))
    page_size = 10
    counts = (
        select(
            Referral.inviter_user_id.label("user_id"),
            func.count(Referral.id).label("total"),
            func.count(Referral.id).filter(Referral.status == "active").label("active"),
        )
        .group_by(Referral.inviter_user_id)
        .subquery()
    )
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(User, counts.c.total, counts.c.active)
                .join(counts, counts.c.user_id == User.id)
                .order_by(counts.c.active.desc(), counts.c.total.desc())
                .offset(page * page_size)
                .limit(page_size + 1)
            )
        ).all()
    has_next = len(rows) > page_size
    rows = rows[:page_size]
    lines = [text(language, "admin_referrers_title")]
    buttons: list[list[InlineKeyboardButton]] = []
    for user, total, active in rows:
        name = f"@{user.username}" if user.username else str(user.telegram_id)
        lines.append(text(language, "admin_referrer_row", name=name, total=total, active=active, pending=total-active))
        buttons.append([InlineKeyboardButton(text=name, callback_data=f"admin_referrer:{user.id}")])
    navigation: list[InlineKeyboardButton] = []
    if page:
        navigation.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin_referrers:{page - 1}"))
    if has_next:
        navigation.append(InlineKeyboardButton(text="➡️", callback_data=f"admin_referrers:{page + 1}"))
    if navigation:
        buttons.append(navigation)
    buttons.extend([
        [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="admin:referrals")],
        [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
    ])
    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_referrals_recent:"))
async def admin_referrals_recent(
    callback: CallbackQuery,
    settings: Settings,
    session_factory: async_sessionmaker,
) -> None:
    if await _deny(callback, settings):
        return
    language = await _admin_language(session_factory, callback.from_user.id)
    page = max(0, int(callback.data.rsplit(":", 1)[1]))
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Referral, User)
                .join(User, User.id == Referral.referred_user_id)
                .order_by(Referral.registered_at.desc())
                .offset(page * 10)
                .limit(11)
            )
        ).all()
    has_next = len(rows) > 10
    lines = [text(language, "admin_referral_recent_title")]
    for referral, user in rows[:10]:
        name = f"@{user.username}" if user.username else str(user.telegram_id)
        lines.append(
            text(
                language,
                "admin_referral_recent_row",
                name=name,
                status=referral.status,
                date=referral.registered_at.strftime("%d.%m.%Y"),
            )
        )
    navigation: list[InlineKeyboardButton] = []
    if page:
        navigation.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin_referrals_recent:{page - 1}"))
    if has_next:
        navigation.append(InlineKeyboardButton(text="➡️", callback_data=f"admin_referrals_recent:{page + 1}"))
    keyboard = ([navigation] if navigation else []) + [
        [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="admin:referrals")],
        [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
    ]
    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_referrer:"))
async def admin_referrer_card(
    callback: CallbackQuery,
    settings: Settings,
    session_factory: async_sessionmaker,
) -> None:
    if await _deny(callback, settings):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    language = await _admin_language(session_factory, callback.from_user.id)
    async with session_factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            await callback.answer(text(language, "admin_user_not_found"), show_alert=True)
            return
        referrals = list(
            (
                await session.execute(
                    select(Referral, User)
                    .join(User, User.id == Referral.referred_user_id)
                    .where(Referral.inviter_user_id == user.id)
                    .order_by(Referral.registered_at.desc())
                )
            ).all()
        )
        earned = int(
            await session.scalar(
                select(func.coalesce(func.sum(ReferralReward.premium_days), 0)).where(
                    ReferralReward.user_id == user.id
                )
            )
            or 0
        )
    active = sum(referral.status == "active" for referral, _ in referrals)
    name = f"@{user.username}" if user.username else (user.display_name or str(user.telegram_id))
    lines = [
        text(
            language,
            "admin_referrer_card",
            name=name,
            telegram_id=user.telegram_id,
            premium_until=user.premium_until.strftime("%d.%m.%Y") if user.premium_until else "—",
            total=len(referrals),
            active=active,
            pending=len(referrals) - active,
            earned=earned,
        )
    ]
    for referral, referred in referrals[:20]:
        referred_name = f"@{referred.username}" if referred.username else str(referred.telegram_id)
        lines.append(
            text(
                language,
                "admin_referral_detail_row",
                name=referred_name,
                onboarding="✅" if referral.onboarding_completed_at else "❌",
                game="✅" if referral.activated_at else "❌",
                status=referral.status,
                date=referral.activated_at.strftime("%d.%m.%Y") if referral.activated_at else "—",
            )
        )
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=text(language, "admin_referral_rewards"),
                        callback_data=f"admin_referral_rewards:{user.id}",
                    )
                ],
                [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="admin_referrers:0")],
                [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_referral_rewards:"))
async def admin_referral_rewards(
    callback: CallbackQuery,
    settings: Settings,
    session_factory: async_sessionmaker,
) -> None:
    if await _deny(callback, settings):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    language = await _admin_language(session_factory, callback.from_user.id)
    async with session_factory() as session:
        rewards = list(
            (
                await session.scalars(
                    select(ReferralReward)
                    .where(ReferralReward.user_id == user_id)
                    .order_by(ReferralReward.created_at.desc())
                    .limit(100)
                )
            ).all()
        )
    lines = [text(language, "admin_referral_rewards_title")]
    lines.extend(
        text(
            language,
            "admin_referral_reward_row",
            days=reward.premium_days,
            key=reward.reward_key,
            date=reward.created_at.strftime("%d.%m.%Y %H:%M"),
        )
        for reward in rewards
    )
    if not rewards:
        lines.append(text(language, "admin_referral_rewards_empty"))
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=text(language, "btn_back"), callback_data=f"admin_referrer:{user_id}")],
                [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:errors")
async def recent_errors(callback: CallbackQuery, settings: Settings, session_factory: async_sessionmaker) -> None:
    if await _deny(callback, settings):
        return
    async with session_factory() as session:
        errors = (await session.scalars(select(SystemError).order_by(SystemError.occurred_at.desc()).limit(10))).all()
    language = await _admin_language(session_factory, callback.from_user.id)
    lines = [text(language, "admin_recent_errors_title")] + (
        [f"• {item.occurred_at:%d.%m %H:%M} · {item.component}: {item.message[:120]}" for item in errors]
        if errors
        else [text(language, "admin_no_errors")]
    )
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=admin_back_keyboard(language),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:refresh_game")
async def refresh_game_start(
    callback: CallbackQuery, state: FSMContext, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(callback, settings):
        return
    await state.set_state(AdminGameRefresh.app_id)
    language = await _admin_language(session_factory, callback.from_user.id)
    await callback.message.answer(text(language, "admin_enter_app_id"), reply_markup=admin_close_keyboard(language))
    await callback.answer()


@router.message(AdminGameRefresh.app_id)
async def refresh_game(
    message: Message, state: FSMContext, settings: Settings, session_factory: async_sessionmaker, steam: SteamProvider
) -> None:
    if await _deny(message, settings):
        return
    language = await _admin_language(session_factory, message.from_user.id)
    try:
        app_id = int(message.text or "")
    except ValueError:
        await message.answer(text(language, "admin_app_id_number"), reply_markup=admin_close_keyboard(language))
        return
    processed = 0
    async with session_factory() as session:
        game = await session.scalar(select(Game).where(Game.steam_app_id == app_id))
        if game is None:
            await message.answer(text(language, "admin_game_missing"), reply_markup=admin_close_keyboard(language))
            await state.clear()
            return
        countries = (
            await session.scalars(
                select(User.country_code)
                .join(WatchRule, WatchRule.user_id == User.id)
                .where(WatchRule.game_id == game.id)
                .distinct()
            )
        ).all()
        for country in countries:
            try:
                region = REGIONS.get(country)
                if region is None:
                    continue
                _, price = await steam.details(app_id, region.steam_country_code, force_refresh=True)
                if price:
                    session.add(
                        PriceSnapshot(
                            game_id=game.id,
                            country_code=country,
                            currency=price.currency,
                            initial_price=price.initial,
                            final_price=price.final,
                            discount_percent=price.discount_percent,
                            checked_at=datetime.now(UTC),
                        )
                    )
                    processed += 1
            except SteamError:
                continue
        await session.commit()
    await state.clear()
    await message.answer(
        text(language, "admin_regions_updated", count=processed), reply_markup=admin_close_keyboard(language)
    )


@router.callback_query(F.data == "admin:broadcast")
async def broadcast_start(
    callback: CallbackQuery, state: FSMContext, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(callback, settings):
        return
    language = await _admin_language(session_factory, callback.from_user.id)
    await state.set_state(AdminBroadcast.content)
    await state.update_data(panel_chat_id=callback.message.chat.id, panel_message_id=callback.message.message_id)
    await callback.message.edit_text(
        text(language, "admin_broadcast_enter"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=text(language, "btn_cancel"), callback_data="admin:broadcast_cancel")],
                [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:deals_broadcast")
async def deals_broadcast_preview(
    callback: CallbackQuery,
    settings: Settings,
    session_factory: async_sessionmaker,
    redis: Redis,
    bot: Bot,
) -> None:
    if await _deny(callback, settings):
        return
    language = await _admin_language(session_factory, callback.from_user.id)
    service = DealBroadcastService(bot, session_factory, redis, settings)
    stats = await service.preview()
    updated = stats["updated"].strftime("%d.%m.%Y %H:%M UTC") if stats["updated"] else text(language, "never")
    await callback.message.edit_text(
        text(
            language,
            "admin_deals_confirm",
            active=stats["active"],
            users=stats["users_with_games"],
            games=stats["games"],
            updated=updated,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=text(language, "admin_deals_start"), callback_data="admin:deals_broadcast_confirm"
                    )
                ],
                [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:deals_broadcast_confirm")
async def deals_broadcast_start(
    callback: CallbackQuery,
    settings: Settings,
    session_factory: async_sessionmaker,
    redis: Redis,
    bot: Bot,
    sync: SyncCoordinator,
) -> None:
    if await _deny(callback, settings):
        return
    language = await _admin_language(session_factory, callback.from_user.id)
    service = DealBroadcastService(bot, session_factory, redis, settings)
    run = await service.create_run(callback.from_user.id)
    if run is None:
        await callback.answer(text(language, "admin_deals_already_running"), show_alert=True)
        return

    async def refresh_due_prices_and_run() -> None:
        try:
            await sync.monitor.run()
        except Exception as error:
            # The broadcast still proceeds from fresh stored rows; stale prices are counted and skipped.
            log.exception(
                "deal_broadcast_price_refresh_failed",
                run_id=run.id,
                admin_id=callback.from_user.id,
                exception_type=type(error).__name__,
            )
        await service.run(run.id)

    asyncio.create_task(refresh_due_prices_and_run(), name=f"deal-broadcast-{run.id}")
    await callback.message.edit_text(
        text(language, "admin_deals_started", run_id=run.id), reply_markup=admin_close_keyboard(language)
    )
    await callback.answer()


@router.message(AdminBroadcast.content)
async def broadcast_preview(
    message: Message, state: FSMContext, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(message, settings):
        return
    language = await _admin_language(session_factory, message.from_user.id)
    content = (message.text or message.caption or "").strip()
    if not content and message.content_type == "text":
        await message.answer(text(language, "admin_broadcast_empty"), reply_markup=admin_close_keyboard(language))
        return
    data = await state.get_data()
    preview_content = content or text(
        language,
        "admin_broadcast_media_preview",
        media_type=message.content_type,
    )
    await state.update_data(
        content=content,
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
        content_type=message.content_type,
    )
    await state.set_state(AdminBroadcast.confirm)
    await message.bot.edit_message_text(
        chat_id=data["panel_chat_id"],
        message_id=data["panel_message_id"],
        text=text(language, "admin_broadcast_preview", content=preview_content),
        parse_mode=None,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=text(language, "btn_confirm"), callback_data="admin:broadcast_confirm")],
                [InlineKeyboardButton(text=text(language, "btn_cancel"), callback_data="admin:broadcast_cancel")],
                [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
            ]
        ),
    )


async def _deliver_admin_broadcast(bot: Bot, user: User, data: dict[str, object]) -> None:
    markup = broadcast_dismiss_keyboard(user.language_code or "ru")
    source_chat_id = data.get("source_chat_id")
    source_message_id = data.get("source_message_id")
    if source_chat_id is not None and source_message_id is not None:
        await bot.copy_message(
            chat_id=user.telegram_id,
            from_chat_id=int(source_chat_id),
            message_id=int(source_message_id),
            reply_markup=markup,
        )
        return
    await bot.send_message(
        user.telegram_id,
        str(data.get("content") or ""),
        parse_mode=None,
        reply_markup=markup,
    )


@router.callback_query(F.data == "admin:broadcast_cancel")
async def broadcast_cancel(
    callback: CallbackQuery, state: FSMContext, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(callback, settings):
        return
    await state.clear()
    language = await _admin_language(session_factory, callback.from_user.id)
    await callback.message.edit_text(text(language, "admin_broadcast_cancelled"), reply_markup=admin_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "admin:test_premium")
async def test_premium(callback: CallbackQuery, settings: Settings, session_factory: async_sessionmaker) -> None:
    if await _deny(callback, settings):
        return
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == callback.from_user.id))
        if user is None:
            await callback.answer(text("ru", "profile_missing"), show_alert=True)
            return
        user_id = user.id
        base = user.premium_until if user.is_premium else datetime.now(UTC)
    await _apply_premium_change(session_factory, user_id, callback.from_user.id, base + timedelta(days=1), "admin_test")
    await callback.answer(
        text(await _admin_language(session_factory, callback.from_user.id), "admin_test_activated"), show_alert=True
    )


def admin_premium_keyboard(language: str, user_id: int, active: bool | None = None) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=text(language, "admin_edit_language"),
                callback_data=f"admin_user_edit:{user_id}:language",
            ),
            InlineKeyboardButton(
                text=text(language, "admin_edit_region"),
                callback_data=f"admin_user_edit:{user_id}:region",
            ),
        ],
        [
            InlineKeyboardButton(
                text=text(language, "admin_edit_currency"),
                callback_data=f"admin_user_edit:{user_id}:currency",
            ),
            InlineKeyboardButton(
                text=text(language, "admin_edit_timezone"),
                callback_data=f"admin_user_edit:{user_id}:timezone",
            ),
        ],
        [
            InlineKeyboardButton(
                text=text(language, "admin_user_transactions"),
                callback_data=f"admin_user_transactions:{user_id}:0",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"+{days} {text(language, 'days_short')}", callback_data=f"admin_premium_add:{user_id}:{days}"
            )
            for days in (1, 3, 7)
        ],
        [
            InlineKeyboardButton(
                text=f"+{days} {text(language, 'days_short')}", callback_data=f"admin_premium_add:{user_id}:{days}"
            )
            for days in (14, 30, 90)
        ],
        [
            InlineKeyboardButton(
                text=f"+{days} {text(language, 'days_short')}", callback_data=f"admin_premium_add:{user_id}:{days}"
            )
            for days in (180, 365)
        ],
        [
            InlineKeyboardButton(
                text=text(language, "admin_premium_manual_days"), callback_data=f"admin_premium_days:{user_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text=text(language, "admin_premium_exact"), callback_data=f"admin_premium_exact:{user_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text=text(language, "admin_premium_history"), callback_data=f"admin_premium_history:{user_id}"
            )
        ],
        [InlineKeyboardButton(text=text(language, "admin_user_delete"), callback_data=f"admin_user_delete:{user_id}")],
    ]
    if active is not False:
        rows.append(
            [
                InlineKeyboardButton(
                    text=text(language, "admin_premium_revoke"), callback_data=f"admin_premium_revoke:{user_id}"
                )
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="admin:premium")],
            [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("admin_user_delete:"))
async def admin_user_delete_confirm(
    callback: CallbackQuery, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(callback, settings):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    language = await _admin_language(session_factory, callback.from_user.id)
    async with session_factory() as session:
        user = await session.get(User, user_id)
    if user is None:
        await callback.answer(text(language, "admin_user_not_found"), show_alert=True)
        return
    await callback.message.edit_text(
        text(
            language,
            "admin_user_delete_confirm",
            user=f"@{user.username}" if user.username else str(user.telegram_id),
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=text(language, "admin_user_delete_confirm_button"),
                        callback_data=f"admin_user_delete_confirm:{user.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=text(language, "btn_cancel"),
                        callback_data=f"admin_premium_user:{user.id}",
                    )
                ],
                [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_user_delete_confirm:"))
async def admin_user_delete_execute(
    callback: CallbackQuery, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(callback, settings):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    language = await _admin_language(session_factory, callback.from_user.id)
    async with session_factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            await callback.answer(text(language, "admin_user_not_found"), show_alert=True)
            return
        telegram_id = user.telegram_id
        await session.delete(user)
        await session.commit()
    log.info(
        "admin_user_deleted",
        admin_telegram_id=callback.from_user.id,
        deleted_user_id=user_id,
        deleted_telegram_id=telegram_id,
    )
    await callback.message.edit_text(
        text(language, "admin_user_deleted"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=text(language, "admin_section_users"), callback_data="admin:users")],
                [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
            ]
        ),
    )
    await callback.answer()


async def _premium_user_card(session_factory: async_sessionmaker, user_id: int, language: str) -> str:
    async with session_factory() as session:
        user = await session.get(User, user_id)
        rules = list(
            (
                await session.scalars(
                    select(WatchRule)
                    .options(selectinload(WatchRule.game))
                    .where(WatchRule.user_id == user.id)
                    .order_by(WatchRule.created_at)
                )
            ).all()
        )
    until = user.premium_until.strftime("%d.%m.%Y %H:%M UTC") if user.premium_until else "—"
    started = user.premium_started_at.strftime("%d.%m.%Y %H:%M UTC") if user.premium_started_at else "—"
    remaining = (
        max(0, int((user.premium_until - datetime.now(UTC)).total_seconds() // 3600)) if user.premium_until else 0
    )
    content = text(
        language,
        "admin_premium_card",
        internal_id=user.id,
        telegram_id=user.telegram_id,
        username=f"@{user.username}" if user.username else "—",
        name=user.display_name or "—",
        status="Premium" if user.is_premium else "Free",
        started=started,
        until=until,
        remaining=remaining,
        source=user.premium_source or "—",
        registered=user.created_at.strftime("%d.%m.%Y"),
        watches=len(rules),
    )
    content += "\n\n" + text(
        language,
        "admin_user_settings",
        language=user.language_code or "—",
        region=user.country_code or "—",
        currency=user.comparison_currency or "—",
        timezone=user.timezone or "—",
        notifications=text(language, "enabled" if user.notifications_enabled else "disabled"),
        activity=user.last_seen_at.strftime("%d.%m.%Y %H:%M UTC") if user.last_seen_at else "—",
    )
    game_lines = [
        text(
            language,
            "admin_user_game_item",
            name=rule.game.name,
            app_id=rule.game.steam_app_id,
            target=rule.max_price or "—",
            discount=rule.min_discount if rule.min_discount is not None else "—",
            added=rule.created_at.strftime("%d.%m.%Y"),
            checked=rule.last_checked_at.strftime("%d.%m.%Y %H:%M") if rule.last_checked_at else "—",
            notified=rule.last_notified_at.strftime("%d.%m.%Y %H:%M") if rule.last_notified_at else "—",
        )
        for rule in rules[:10]
    ]
    content += "\n\n" + text(
        language,
        "admin_user_games",
        items="\n".join(game_lines) or text(language, "admin_empty"),
        suffix=text(language, "admin_user_games_more", count=len(rules) - 10) if len(rules) > 10 else "",
    )
    return content


@router.callback_query(F.data.startswith("admin_user_edit:"))
async def admin_user_edit_start(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
    session_factory: async_sessionmaker,
) -> None:
    if await _deny(callback, settings):
        return
    _, raw_user_id, field = callback.data.split(":")
    if field not in {"language", "region", "currency", "timezone"}:
        await callback.answer(text("ru", "invalid_value"), show_alert=True)
        return
    language = await _admin_language(session_factory, callback.from_user.id)
    await state.set_state(AdminUserEdit.value)
    await state.update_data(
        user_id=int(raw_user_id),
        field=field,
        panel_chat_id=callback.message.chat.id,
        panel_message_id=callback.message.message_id,
    )
    await callback.message.edit_text(
        text(language, "admin_user_edit_prompt", field=text(language, f"admin_field_{field}")),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=text(language, "btn_cancel"),
                        callback_data=f"admin_premium_user:{raw_user_id}",
                    )
                ],
                [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
            ]
        ),
    )
    await callback.answer()


@router.message(AdminUserEdit.value)
async def admin_user_edit_save(
    message: Message,
    state: FSMContext,
    settings: Settings,
    session_factory: async_sessionmaker,
    currency: CurrencyService,
) -> None:
    if await _deny(message, settings):
        return
    data = await state.get_data()
    user_id = int(data["user_id"])
    field = data["field"]
    raw_value = (message.text or "").strip()
    value = raw_value
    if field == "language" and value not in LANGUAGES:
        value = ""
    elif field == "region":
        value = value.upper()
        if value not in REGIONS:
            value = ""
    elif field == "currency":
        value = value.upper()
        if value not in COMPARISON_CURRENCIES:
            value = ""
    elif field == "timezone":
        value = normalize_utc_offset(value) or value
        if value not in TIMEZONES and normalize_utc_offset(value) is None:
            value = ""
    language = await _admin_language(session_factory, message.from_user.id)
    if not value:
        await message.answer(text(language, "admin_user_edit_invalid"))
        return
    attribute = {
        "language": "language_code",
        "region": "country_code",
        "currency": "comparison_currency",
        "timezone": "timezone",
    }[field]
    async with session_factory() as session:
        user = await session.get(User, user_id)
        if user is None:
            await state.clear()
            await message.answer(text(language, "admin_user_not_found"))
            return
        old_value = getattr(user, attribute)
        if field in {"region", "currency"}:
            source_default = REGIONS[user.country_code].currency
            target_currency = REGIONS[value].currency if field == "region" else value
            try:
                await convert_user_money_conditions(
                    session,
                    currency,
                    user.id,
                    source_default,
                    target_currency,
                )
            except CurrencyError:
                await message.answer(text(language, "condition_conversion_failed"))
                return
        setattr(user, attribute, value)
        session.add(
            AdminUserAudit(
                user_id=user.id,
                admin_telegram_id=message.from_user.id,
                field=field,
                old_value=str(old_value) if old_value is not None else None,
                new_value=value,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
    await state.clear()
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    content = await _premium_user_card(session_factory, user_id, language)
    try:
        await message.bot.edit_message_text(
            chat_id=data["panel_chat_id"],
            message_id=data["panel_message_id"],
            text=content,
            reply_markup=admin_premium_keyboard(language, user_id),
        )
    except TelegramBadRequest:
        await message.answer(content, reply_markup=admin_premium_keyboard(language, user_id))


@router.callback_query(F.data.startswith("admin_user_transactions:"))
async def admin_user_transactions(
    callback: CallbackQuery,
    settings: Settings,
    session_factory: async_sessionmaker,
) -> None:
    if await _deny(callback, settings):
        return
    _, raw_user_id, raw_page = callback.data.split(":")
    user_id, page = int(raw_user_id), max(0, int(raw_page))
    page_size = 10
    async with session_factory() as session:
        payments = list(
            (
                await session.scalars(
                    select(Payment)
                    .where(Payment.user_id == user_id)
                    .order_by(Payment.paid_at.desc())
                    .offset(page * page_size)
                    .limit(page_size + 1)
                )
            ).all()
        )
    language = await _admin_language(session_factory, callback.from_user.id)
    has_next = len(payments) > page_size
    items = payments[:page_size]
    content = text(
        language,
        "admin_transactions_list",
        page=page + 1,
        items="\n".join(
            f"• #{item.id} · {item.paid_at:%d.%m.%Y} · {item.amount_stars} ⭐ · {item.tariff} · {item.status}"
            for item in items
        )
        or text(language, "admin_empty"),
    )
    navigation = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"admin_user_transactions:{user_id}:{page - 1}",
            )
        )
    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"admin_user_transactions:{user_id}:{page + 1}",
            )
        )
    rows = [navigation] if navigation else []
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=text(language, "btn_back"),
                    callback_data=f"admin_premium_user:{user_id}",
                )
            ],
            [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
        ]
    )
    await callback.message.edit_text(content, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data == "admin:premium")
async def admin_premium_root(callback: CallbackQuery, settings: Settings, session_factory: async_sessionmaker) -> None:
    if await _deny(callback, settings):
        return
    language = await _admin_language(session_factory, callback.from_user.id)
    await callback.message.edit_text(
        text(language, "admin_premium_root"), reply_markup=admin_premium_root_keyboard(language)
    )
    await callback.answer()


def _admin_user_label(user: User, language: str) -> str:
    identity = f"@{user.username}" if user.username else user.display_name or f"ID {user.telegram_id}"
    status = (
        text(language, "admin_premium_until_short", value=f"{user.premium_until:%d.%m.%Y}")
        if user.is_premium
        else "Free"
    )
    return f"{identity[:35]} · {status}"


async def _admin_users_markup(users: list[User], language: str, page: int, pages: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=_admin_user_label(user, language), callback_data=f"admin_premium_user:{user.id}")]
        for user in users
    ]
    navigation = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(text=text(language, "btn_previous"), callback_data=f"admin_premium_users:{page - 1}")
        )
    if page + 1 < pages:
        navigation.append(
            InlineKeyboardButton(text=text(language, "btn_next"), callback_data=f"admin_premium_users:{page + 1}")
        )
    if navigation:
        rows.append(navigation)
    rows.extend(
        [
            [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="admin:premium")],
            [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("admin_premium_users:") | F.data.startswith("admin_premium_active:"))
async def admin_premium_users(callback: CallbackQuery, settings: Settings, session_factory: async_sessionmaker) -> None:
    if await _deny(callback, settings):
        return
    page = max(0, int(callback.data.rsplit(":", 1)[1]))
    active_only = callback.data.startswith("admin_premium_active:")
    page_size = 8
    async with session_factory() as session:
        query = select(User)
        count_query = select(func.count()).select_from(User)
        if active_only:
            condition = User.premium_until > datetime.now(UTC)
            query = query.where(condition)
            count_query = count_query.where(condition)
        total = await session.scalar(count_query) or 0
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages - 1)
        users = list(
            (
                await session.scalars(query.order_by(User.created_at.desc()).offset(page * page_size).limit(page_size))
            ).all()
        )
    language = await _admin_language(session_factory, callback.from_user.id)
    await callback.message.edit_text(
        text(language, "admin_premium_users_title", page=page + 1, pages=pages),
        reply_markup=await _admin_users_markup(users, language, page, pages),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_premium_user:"))
async def admin_premium_user_open(
    callback: CallbackQuery, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(callback, settings):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    language = await _admin_language(session_factory, callback.from_user.id)
    async with session_factory() as session:
        user = await session.get(User, user_id)
    if user is None:
        await callback.answer(text(language, "admin_user_not_found"), show_alert=True)
        return
    await callback.message.edit_text(
        await _premium_user_card(session_factory, user.id, language),
        reply_markup=admin_premium_keyboard(language, user.id, user.is_premium),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:premium_search")
async def admin_premium_search_start(
    callback: CallbackQuery, state: FSMContext, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(callback, settings):
        return
    language = await _admin_language(session_factory, callback.from_user.id)
    await state.set_state(AdminPremium.search)
    await state.set_data({"prompt_chat_id": callback.message.chat.id, "prompt_message_id": callback.message.message_id})
    await callback.message.edit_text(
        text(language, "admin_premium_search"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=text(language, "btn_cancel"), callback_data="admin:premium")],
                [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
            ]
        ),
    )
    await callback.answer()


@router.message(AdminPremium.search)
async def admin_premium_search(
    message: Message, state: FSMContext, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(message, settings):
        return
    query = (message.text or "").strip().removeprefix("@")
    language = await _admin_language(session_factory, message.from_user.id)
    state_data = await state.get_data()
    async with session_factory() as session:
        conditions = [
            func.lower(User.username) == query.lower(),
            func.lower(User.display_name).contains(query.lower()),
            User.referral_code == query,
        ]
        if query.isdigit():
            number = int(query)
            conditions += [User.telegram_id == number, User.id == number]
        users = list((await session.scalars(select(User).where(or_(*conditions)).limit(10))).all())
    prompt_chat_id = state_data.get("prompt_chat_id")
    prompt_message_id = state_data.get("prompt_message_id")
    if prompt_chat_id and prompt_message_id:
        try:
            await message.bot.delete_message(prompt_chat_id, prompt_message_id)
        except TelegramBadRequest:
            pass
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    await state.clear()
    if not users:
        await message.answer(text(language, "admin_user_not_found"), reply_markup=admin_close_keyboard(language))
        return
    if len(users) > 1:
        await message.answer(
            text(language, "admin_premium_ambiguous"),
            reply_markup=await _admin_users_markup(users, language, 0, 1),
        )
        return
    user = users[0]
    await message.answer(
        await _premium_user_card(session_factory, user.id, language),
        reply_markup=admin_premium_keyboard(language, user.id, user.is_premium),
    )


async def _apply_premium_change(
    session_factory: async_sessionmaker,
    user_id: int,
    admin_id: int,
    new_until: datetime | None,
    action: str,
) -> None:
    async with session_factory() as session:
        user = await session.get(User, user_id)
        old_until = user.premium_until
        user.premium_until = new_until
        user.plan = Plan.PREMIUM if new_until and new_until > datetime.now(UTC) else Plan.FREE
        if user.plan == Plan.PREMIUM:
            user.premium_started_at = user.premium_started_at or datetime.now(UTC)
            user.premium_expired_notified_at = None
            user.premium_source = "admin_test" if action == "admin_test" else "admin"
        session.add(
            PremiumAudit(
                user_id=user.id,
                admin_telegram_id=admin_id,
                action=action,
                old_until=old_until,
                new_until=new_until,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()


@router.callback_query(F.data.startswith("admin_premium_add:"))
async def admin_premium_add(callback: CallbackQuery, settings: Settings, session_factory: async_sessionmaker) -> None:
    if await _deny(callback, settings):
        return
    _, user_id, days = callback.data.split(":")
    async with session_factory() as session:
        user = await session.get(User, int(user_id))
        base = user.premium_until if user.is_premium else datetime.now(UTC)
    await _apply_premium_change(
        session_factory, int(user_id), callback.from_user.id, base + timedelta(days=int(days)), "extend"
    )
    language = await _admin_language(session_factory, callback.from_user.id)
    await callback.message.edit_text(
        await _premium_user_card(session_factory, int(user_id), language),
        reply_markup=admin_premium_keyboard(language, int(user_id)),
    )
    await callback.answer(text(language, "admin_premium_saved"))


@router.callback_query(F.data.startswith("admin_premium_revoke:"))
async def admin_premium_revoke_confirm(
    callback: CallbackQuery, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(callback, settings):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    language = await _admin_language(session_factory, callback.from_user.id)
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text(language, "btn_confirm"), callback_data=f"admin_premium_revoke_confirm:{user_id}"
                )
            ],
            [InlineKeyboardButton(text=text(language, "btn_cancel"), callback_data=f"admin_premium_user:{user_id}")],
            [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
        ]
    )
    await callback.message.edit_text(text(language, "admin_premium_revoke_confirm"), reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_premium_revoke_confirm:"))
async def admin_premium_revoke(
    callback: CallbackQuery, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(callback, settings):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    await _apply_premium_change(session_factory, user_id, callback.from_user.id, None, "revoke")
    language = await _admin_language(session_factory, callback.from_user.id)
    await callback.message.edit_text(
        await _premium_user_card(session_factory, user_id, language),
        reply_markup=admin_premium_keyboard(language, user_id),
    )
    await callback.answer(text(language, "admin_premium_saved"))


@router.callback_query(F.data.startswith("admin_premium_user:"))
async def admin_premium_user(callback: CallbackQuery, settings: Settings, session_factory: async_sessionmaker) -> None:
    if await _deny(callback, settings):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    language = await _admin_language(session_factory, callback.from_user.id)
    await callback.message.edit_text(
        await _premium_user_card(session_factory, user_id, language),
        reply_markup=admin_premium_keyboard(language, user_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_premium_history:"))
async def admin_premium_history(
    callback: CallbackQuery, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(callback, settings):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    language = await _admin_language(session_factory, callback.from_user.id)
    async with session_factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(PremiumAudit)
                    .where(PremiumAudit.user_id == user_id)
                    .order_by(PremiumAudit.created_at.desc())
                    .limit(20)
                )
            ).all()
        )
    items = "\n".join(
        f"• {row.created_at:%d.%m.%Y %H:%M} · {row.action} · {row.admin_telegram_id}" for row in rows
    ) or text(language, "admin_premium_history_empty")
    await callback.message.edit_text(
        text(language, "admin_premium_history_screen", items=items),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=text(language, "btn_back"), callback_data=f"admin_premium_user:{user_id}")],
                [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_premium_days:") | F.data.startswith("admin_premium_exact:"))
async def admin_premium_manual_start(
    callback: CallbackQuery, state: FSMContext, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(callback, settings):
        return
    prefix, user_id = callback.data.split(":")
    language = await _admin_language(session_factory, callback.from_user.id)
    await state.update_data(premium_user_id=int(user_id))
    if prefix.endswith("days"):
        await state.set_state(AdminPremium.manual_days)
        prompt = text(language, "admin_premium_days_prompt")
    else:
        await state.set_state(AdminPremium.exact_until)
        prompt = text(language, "admin_premium_exact_prompt")
    await callback.message.edit_text(prompt, reply_markup=admin_close_keyboard(language))
    await callback.answer()


@router.message(AdminPremium.manual_days)
async def admin_premium_manual_days(
    message: Message, state: FSMContext, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(message, settings):
        return
    language = await _admin_language(session_factory, message.from_user.id)
    try:
        days = int(message.text or "")
        if not 1 <= days <= 3650:
            raise ValueError
    except ValueError:
        await message.answer(text(language, "admin_premium_days_invalid"), reply_markup=admin_close_keyboard(language))
        return
    data = await state.get_data()
    async with session_factory() as session:
        user = await session.get(User, data["premium_user_id"])
        base = user.premium_until if user.is_premium else datetime.now(UTC)
    await _apply_premium_change(session_factory, user.id, message.from_user.id, base + timedelta(days=days), "extend")
    await state.clear()
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    await message.answer(
        await _premium_user_card(session_factory, user.id, language),
        reply_markup=admin_premium_keyboard(language, user.id),
    )


@router.message(AdminPremium.exact_until)
async def admin_premium_exact_input(
    message: Message, state: FSMContext, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(message, settings):
        return
    language = await _admin_language(session_factory, message.from_user.id)
    try:
        new_until = datetime.strptime(message.text or "", "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
    except ValueError:
        await message.answer(text(language, "admin_premium_exact_invalid"), reply_markup=admin_close_keyboard(language))
        return
    data = await state.get_data()
    await state.update_data(new_until=new_until.isoformat())
    await state.set_state(AdminPremium.confirm)
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    await message.answer(
        text(language, "admin_premium_exact_confirm", date=new_until.strftime("%d.%m.%Y %H:%M UTC")),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=text(language, "btn_confirm"), callback_data="admin_premium_exact_confirm")],
                [
                    InlineKeyboardButton(
                        text=text(language, "btn_cancel"), callback_data=f"admin_premium_user:{data['premium_user_id']}"
                    )
                ],
                [InlineKeyboardButton(text=text(language, "admin_close"), callback_data="admin:close")],
            ]
        ),
    )


@router.callback_query(AdminPremium.confirm, F.data == "admin_premium_exact_confirm")
async def admin_premium_exact_confirm(
    callback: CallbackQuery, state: FSMContext, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(callback, settings):
        return
    data = await state.get_data()
    user_id = data["premium_user_id"]
    new_until = datetime.fromisoformat(data["new_until"])
    await _apply_premium_change(session_factory, user_id, callback.from_user.id, new_until, "set_until")
    await state.clear()
    language = await _admin_language(session_factory, callback.from_user.id)
    await callback.message.edit_text(
        await _premium_user_card(session_factory, user_id, language),
        reply_markup=admin_premium_keyboard(language, user_id),
    )
    await callback.answer(text(language, "admin_premium_saved"))


@router.callback_query(AdminBroadcast.confirm, F.data == "admin:broadcast_confirm")
async def broadcast_confirm(
    callback: CallbackQuery, state: FSMContext, settings: Settings, session_factory: async_sessionmaker, bot: Bot
) -> None:
    if await _deny(callback, settings):
        return
    broadcast_data = await state.get_data()
    await state.clear()
    async with session_factory() as session:
        recipients = list((await session.scalars(select(User))).all())
    sent = failed = 0
    language = await _admin_language(session_factory, callback.from_user.id)
    await callback.answer(text(language, "admin_broadcast_started"))
    await callback.message.edit_text(
        text(language, "admin_broadcast_running"), reply_markup=admin_close_keyboard(language)
    )
    for user in recipients:
        try:
            await _deliver_admin_broadcast(bot, user, broadcast_data)
            sent += 1
        except TelegramRetryAfter as error:
            await asyncio.sleep(error.retry_after)
            try:
                await _deliver_admin_broadcast(bot, user, broadcast_data)
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            failed += 1
        except Exception:
            failed += 1
            log.exception(
                "admin_broadcast_delivery_failed",
                telegram_id=user.telegram_id,
                content_type=broadcast_data.get("content_type"),
            )
        await asyncio.sleep(0.04)
    if broadcast_data.get("source_chat_id") and broadcast_data.get("source_message_id"):
        try:
            await bot.delete_message(
                int(broadcast_data["source_chat_id"]),
                int(broadcast_data["source_message_id"]),
            )
        except TelegramBadRequest:
            pass
    await callback.message.edit_text(
        text(language, "admin_broadcast_done", sent=sent, failed=failed), reply_markup=admin_keyboard(language)
    )


@router.callback_query(F.data == "admin:add_giveaway")
async def add_giveaway(
    callback: CallbackQuery, state: FSMContext, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(callback, settings):
        return
    await state.set_state(AdminGiveaway.title)
    language = await _admin_language(session_factory, callback.from_user.id)
    await callback.message.answer(
        text(language, "admin_giveaway_title_prompt"), reply_markup=admin_close_keyboard(language)
    )
    await callback.answer()


@router.message(AdminGiveaway.title)
async def giveaway_title(
    message: Message, state: FSMContext, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(message, settings):
        return
    await state.update_data(title=message.text)
    await state.set_state(AdminGiveaway.url)
    language = await _admin_language(session_factory, message.from_user.id)
    await message.answer(text(language, "admin_giveaway_url_prompt"), reply_markup=admin_close_keyboard(language))


@router.message(AdminGiveaway.url)
async def giveaway_url(
    message: Message, state: FSMContext, settings: Settings, session_factory: async_sessionmaker
) -> None:
    if await _deny(message, settings):
        return
    if not message.text or not message.text.startswith("https://"):
        language = await _admin_language(session_factory, message.from_user.id)
        await message.answer(text(language, "admin_https_required"), reply_markup=admin_close_keyboard(language))
        return
    data = await state.get_data()
    async with session_factory() as session:
        session.add(
            Giveaway(
                title=data["title"],
                url=message.text,
                kind=GiveawayKind.KEEP,
                approved=True,
                active=True,
                source="manual",
                last_seen_at=datetime.now(UTC),
            )
        )
        await session.commit()
    await state.clear()
    language = await _admin_language(session_factory, message.from_user.id)
    await message.answer(text(language, "admin_giveaway_added"), reply_markup=admin_close_keyboard(language))
