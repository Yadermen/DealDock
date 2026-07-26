import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputMediaPhoto,
    InputTextMessageContent,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from steam_radar.bot.keyboards import (
    active_premium_keyboard,
    analytics_keyboard,
    back_keyboard,
    comparison_currency_keyboard,
    comparison_groups_keyboard,
    comparison_regions_keyboard,
    comparison_result_keyboard,
    deals_analytics_keyboard,
    deals_filter_input_keyboard,
    deals_filters_keyboard,
    deals_keyboard,
    deals_sort_keyboard,
    digest_keyboard,
    digest_kind_keyboard,
    dismiss_keyboard,
    games_keyboard,
    giveaway_types_keyboard,
    giveaways_keyboard,
    hour_keyboard,
    info_keyboard,
    info_page_keyboard,
    invoice_keyboard,
    language_keyboard,
    main_keyboard,
    manual_timezone_keyboard,
    minute_keyboard,
    premium_filters_keyboard,
    premium_games_keyboard,
    premium_gate_keyboard,
    premium_info_return_keyboard,
    premium_keyboard,
    price_history_keyboard,
    profile_currency_keyboard,
    profile_keyboard,
    quiet_hours_keyboard,
    referral_leaderboard_keyboard,
    referrals_keyboard,
    region_groups_keyboard,
    region_keyboard,
    rule_keyboard,
    timezone_groups_keyboard,
    timezone_keyboard,
    watch_card_keyboard,
    watch_list_keyboard,
    weekday_keyboard,
)
from steam_radar.bot.states import (
    AddGame,
    DealsFilterSetup,
    EditWatch,
    Onboarding,
    PremiumFilterSetup,
    RegionCompare,
    TimezoneSetup,
)
from steam_radar.config import Settings
from steam_radar.constants import (
    ADMIN_TEST_PREMIUM_CODE,
    ADMIN_TEST_PREMIUM_DAYS,
    ADMIN_TEST_PREMIUM_STARS,
    COMPARISON_CURRENCIES,
    FREE_GAME_LIMIT,
    LANGUAGES,
    MAX_COMPARISON_REGIONS,
    PREMIUM_GAME_LIMIT,
    PREMIUM_PRICES,
    REFERRAL_LEVELS,
    REGIONS,
)
from steam_radar.db.models import ExternalHistoricalLow, Game, Giveaway, Payment, Plan, PriceSnapshot, User, WatchRule
from steam_radar.db.repositories import get_or_create_game, get_or_create_user, get_user, watch_count
from steam_radar.i18n import text
from steam_radar.services.analytics import AnalyticsSnapshot, calculate_purchase_score
from steam_radar.services.condition_currency import convert_user_money_conditions
from steam_radar.services.currency import CurrencyError, CurrencyService, ExchangeRates
from steam_radar.services.game_search import GameSearchService
from steam_radar.services.itad import HistoricalLowSync, ITADError
from steam_radar.services.price_analysis import PriceAnalyticsService
from steam_radar.services.price_chart import PriceChartService
from steam_radar.services.price_history import PriceHistoryService
from steam_radar.services.pricing import NormalizedPrice, format_money, format_price_card
from steam_radar.services.referrals import ReferralDashboard, ReferralService
from steam_radar.services.region_comparison import CachedRegionalPrice, RegionalPriceComparison
from steam_radar.services.steam import SteamError, SteamGame, SteamPrice, SteamProvider
from steam_radar.services.timezones import normalize_utc_offset, timezone_from_name

router = Router(name="user")
log = structlog.get_logger()


def _parse_compare_callback(data: str) -> tuple[str, int]:
    """Parse the explicit callback format and the legacy rule-id format."""
    parts = data.split(":")
    try:
        if len(parts) == 2 and parts[0] == "premium_compare":
            return "rule", int(parts[1])
        if len(parts) == 3 and parts[0] == "premium_compare" and parts[1] in {"rule", "steam"}:
            return parts[1], int(parts[2])
    except ValueError as error:
        raise ValueError("invalid comparison identifier") from error
    raise ValueError("invalid comparison callback format")


def _comparison_rule_error(user: User | None, rule: WatchRule | None) -> str | None:
    if user is None:
        return "profile_missing"
    if rule is None or rule.game is None:
        return "compare_game_missing"
    if rule.user_id != user.id:
        return "compare_not_owned"
    if not user.is_premium:
        return "premium_required"
    if not rule.game.steam_app_id:
        return "compare_app_id_missing"
    return None


async def _user(session: AsyncSession, telegram_id: int) -> User | None:
    return await get_user(session, telegram_id)


async def _show_premium_gate(
    callback: CallbackQuery,
    language: str,
    back_callback: str,
    feature: str = "tools",
) -> None:
    months = max(PREMIUM_PRICES)
    stars = PREMIUM_PRICES[months]
    await callback.message.edit_text(
        text(language, f"premium_gate_{feature}"),
        reply_markup=premium_gate_keyboard(language, back_callback, months, stars),
    )
    await callback.answer()


def _info_frequency(language: str, hours: int) -> str:
    key = "info_frequency_hourly" if hours == 1 else "info_frequency_hours"
    return text(language, key, hours=hours)


@router.message(CommandStart())
async def start(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker,
    referral_service: ReferralService,
) -> None:
    parts = (message.text or "").split(maxsplit=1)
    referral_code = parts[1].strip() if len(parts) == 2 else None
    async with session_factory() as session:
        existing = await get_user(session, message.from_user.id)
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username, message.from_user.full_name
        )
        if existing is None:
            await referral_service.register_pending(session, user, referral_code)
        await session.commit()
        if user.language_code and user.country_code:
            await message.answer(text(user.language_code, "menu"), reply_markup=main_keyboard(user.language_code))
            return
    await state.set_state(Onboarding.language)
    await message.answer(text("ru", "welcome_multilingual"), reply_markup=language_keyboard())


@router.callback_query(Onboarding.language, F.data.startswith("lang:"))
async def choose_language(callback: CallbackQuery, state: FSMContext) -> None:
    language = callback.data.split(":", 1)[1]
    await state.update_data(language=language)
    await state.set_state(Onboarding.region_group)
    await callback.message.edit_text(
        text(language, "choose_region_group"),
        reply_markup=region_groups_keyboard(language, onboarding=True),
    )
    await callback.answer()


@router.callback_query(Onboarding.region_group, F.data.startswith("region_group:"))
async def choose_onboarding_region_group(callback: CallbackQuery, state: FSMContext) -> None:
    group = callback.data.split(":", 1)[1]
    data = await state.get_data()
    language = data.get("language", "ru")
    if group not in {region.geo_group for region in REGIONS.values()}:
        await callback.answer(text(language, "invalid_value"), show_alert=True)
        return
    await state.set_state(Onboarding.region)
    await callback.message.edit_text(
        text(language, "choose_region"),
        reply_markup=region_keyboard(language, group=group, onboarding=True),
    )
    await callback.answer()


@router.callback_query(Onboarding.region, F.data == "region_groups")
async def onboarding_region_groups(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    language = data.get("language", "ru")
    await state.set_state(Onboarding.region_group)
    await callback.message.edit_text(
        text(language, "choose_region_group"),
        reply_markup=region_groups_keyboard(language, onboarding=True),
    )
    await callback.answer()


@router.callback_query(Onboarding.region, F.data.startswith("region:"))
async def choose_region(callback: CallbackQuery, state: FSMContext, session_factory: async_sessionmaker) -> None:
    country = callback.data.split(":", 1)[1]
    data = await state.get_data()
    language = data.get("language", "ru")
    if country not in REGIONS:
        await callback.answer(text(language, "invalid_value"), show_alert=True)
        return
    await state.update_data(country=country)
    await state.set_state(Onboarding.timezone_group)
    await callback.message.edit_text(
        text(language, "onboarding_timezone_prompt"),
        reply_markup=timezone_groups_keyboard(language, onboarding=True),
    )
    await callback.answer()


@router.callback_query(Onboarding.timezone_group, F.data.startswith("timezone_group:"))
async def choose_onboarding_timezone_group(callback: CallbackQuery, state: FSMContext) -> None:
    group = callback.data.split(":", 1)[1]
    data = await state.get_data()
    language = data.get("language", "ru")
    if group not in {region.geo_group for region in REGIONS.values()}:
        await callback.answer(text(language, "invalid_value"), show_alert=True)
        return
    await state.set_state(Onboarding.timezone)
    await callback.message.edit_text(
        text(language, "choose_timezone"),
        reply_markup=timezone_keyboard(language, group, onboarding=True),
    )
    await callback.answer()


async def _finish_onboarding(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker,
    timezone_name: str,
    referral_service: ReferralService,
) -> None:
    data = await state.get_data()
    language = data.get("language", "ru")
    country = data.get("country")
    if country not in REGIONS:
        await callback.answer(text(language, "invalid_value"), show_alert=True)
        return
    async with session_factory() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name,
        )
        user.language_code = language
        user.country_code = country
        user.timezone = timezone_name
        await referral_service.mark_onboarding_completed(session, user)
        await session.commit()
    await state.clear()
    await callback.message.edit_text(
        text(
            language,
            "ready_with_timezone",
            region=text(language, f"region_{country.lower()}"),
            timezone=timezone_name,
        ),
        reply_markup=main_keyboard(language),
    )
    await callback.answer()


@router.callback_query(Onboarding.timezone, F.data.startswith("timezone:"))
async def choose_onboarding_timezone(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker,
    referral_service: ReferralService,
) -> None:
    timezone_name = callback.data.split(":", 1)[1]
    if timezone_name not in {region.timezone for region in REGIONS.values()}:
        data = await state.get_data()
        await callback.answer(text(data.get("language", "ru"), "invalid_value"), show_alert=True)
        return
    await _finish_onboarding(callback, state, session_factory, timezone_name, referral_service)


@router.callback_query(Onboarding.timezone, F.data == "timezone_groups")
async def onboarding_timezone_groups(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    language = data.get("language", "ru")
    await state.set_state(Onboarding.timezone_group)
    await callback.message.edit_text(
        text(language, "onboarding_timezone_prompt"),
        reply_markup=timezone_groups_keyboard(language, onboarding=True),
    )
    await callback.answer()


@router.callback_query(Onboarding.timezone_group, F.data == "timezone:manual")
@router.callback_query(Onboarding.timezone, F.data == "timezone:manual")
async def onboarding_manual_timezone(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    language = data.get("language", "ru")
    await state.set_state(Onboarding.manual_timezone)
    await state.update_data(
        timezone_prompt_chat_id=callback.message.chat.id,
        timezone_prompt_message_id=callback.message.message_id,
    )
    await callback.message.edit_text(
        text(language, "timezone_manual_prompt"),
        reply_markup=manual_timezone_keyboard(language, onboarding=True),
    )
    await callback.answer()


@router.callback_query(Onboarding.manual_timezone, F.data == "onboarding:timezone")
async def onboarding_manual_timezone_back(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    language = data.get("language", "ru")
    await state.set_state(Onboarding.timezone_group)
    await callback.message.edit_text(
        text(language, "onboarding_timezone_prompt"),
        reply_markup=timezone_groups_keyboard(language, onboarding=True),
    )
    await callback.answer()


@router.callback_query(Onboarding.timezone_group, F.data == "onboarding:cancel")
@router.callback_query(Onboarding.timezone, F.data == "onboarding:cancel")
@router.callback_query(Onboarding.manual_timezone, F.data == "onboarding:cancel")
async def onboarding_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    language = data.get("language", "ru")
    await state.set_state(Onboarding.timezone_group)
    await callback.message.edit_text(
        text(language, "onboarding_timezone_prompt"),
        reply_markup=timezone_groups_keyboard(language, onboarding=True),
    )
    await callback.answer(text(language, "onboarding_required"), show_alert=True)


@router.callback_query(F.data == "ui:close")
async def close_current_message(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        await callback.message.delete()
    except (TelegramBadRequest, TelegramForbiddenError):
        pass


@router.message(Command("menu"))
async def menu(message: Message, state: FSMContext, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, message.from_user.id)
    language = user.language_code if user else "ru"
    if not user or not user.language_code or not user.country_code:
        await state.set_state(Onboarding.language)
        await message.answer(
            text(language, "onboarding_required") + "\n\n" + text("ru", "welcome_multilingual"),
            reply_markup=language_keyboard(),
        )
        return
    await message.answer(text(language, "menu"), reply_markup=main_keyboard(language))


@router.message(Command("games"))
async def games_command(message: Message, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, message.from_user.id)
        rules = (
            (
                await session.scalars(
                    select(WatchRule).options(selectinload(WatchRule.game)).where(WatchRule.user_id == user.id)
                )
            ).all()
            if user
            else []
        )
    language = user.language_code if user else "ru"
    content = text(language, "my_games") if rules else text(language, "no_games")
    await message.answer(content, reply_markup=watch_list_keyboard(list(rules), language))


@router.message(Command("watch"))
async def watch_command(message: Message, state: FSMContext, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, message.from_user.id)
    if not user or not user.country_code:
        await message.answer(text("ru", "start_first"))
        return
    panel = await message.answer(
        text(user.language_code, "search_prompt"), reply_markup=back_keyboard("home", user.language_code)
    )
    await state.set_state(AddGame.query)
    await state.update_data(panel_chat_id=panel.chat.id, panel_message_id=panel.message_id)


@router.message(Command("settings"))
async def settings_command(message: Message, session_factory: async_sessionmaker, settings: Settings) -> None:
    async with session_factory() as session:
        user = await _user(session, message.from_user.id)
    if not user:
        await message.answer(text("ru", "profile_missing"))
        return
    content, markup = _profile_screen(user, settings.app_timezone)
    await message.answer(content, reply_markup=markup)


@router.message(Command("premium"))
async def premium_command(message: Message, session_factory: async_sessionmaker, settings: Settings) -> None:
    async with session_factory() as session:
        user = await _user(session, message.from_user.id)
        tracked = await watch_count(session, user.id) if user else 0
    language = user.language_code if user else "ru"
    content, keyboard = _premium_screen(user, language, settings.telegram_payment_test_mode, tracked)
    await message.answer(content, reply_markup=keyboard)


@router.message(Command("deals", "free"))
async def section_command(message: Message, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, message.from_user.id)
    language = user.language_code if user else "ru"
    section = "menu:giveaways" if message.text.startswith("/free") else "menu:deals"
    label = text(language, "open_giveaways" if section.endswith("giveaways") else "open_deals")
    await message.answer(
        label,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=label, callback_data=section)],
                back_keyboard("home", language).inline_keyboard[0],
            ]
        ),
    )


@router.message(Command("help"))
async def help_command(message: Message, session_factory: async_sessionmaker) -> None:
    await message.answer(text(await _language(session_factory, message.from_user.id), "help"))


@router.message(Command("terms"))
async def terms_command(message: Message, session_factory: async_sessionmaker) -> None:
    await message.answer(text(await _language(session_factory, message.from_user.id), "terms"))


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery, state: FSMContext, session_factory: async_sessionmaker) -> None:
    current_state = await state.get_state()
    if current_state and current_state.startswith("Onboarding:"):
        data = await state.get_data()
        language = data.get("language", "ru")
        if current_state == Onboarding.language.state:
            await callback.message.edit_text(
                text(language, "onboarding_required") + "\n\n" + text("ru", "welcome_multilingual"),
                reply_markup=language_keyboard(),
            )
        elif current_state in {Onboarding.region_group.state, Onboarding.region.state}:
            await state.set_state(Onboarding.region_group)
            await callback.message.edit_text(
                text(language, "choose_region_group"),
                reply_markup=region_groups_keyboard(language, onboarding=True),
            )
        else:
            await state.set_state(Onboarding.timezone_group)
            await callback.message.edit_text(
                text(language, "onboarding_timezone_prompt"),
                reply_markup=timezone_groups_keyboard(language, onboarding=True),
            )
        await callback.answer(text(language, "onboarding_required"), show_alert=True)
        return
    await state.clear()
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    language = user.language_code if user else "ru"
    await callback.message.edit_text(text(language, "menu"), reply_markup=main_keyboard(language))
    await callback.answer()


def _referral_progress(language: str, dashboard: ReferralDashboard) -> str:
    level = dashboard.next_level
    if level is None:
        return text(language, "referral_progress_complete")
    filled = min(10, int(dashboard.active / level.active_referrals * 10))
    bar = "█" * filled + "░" * (10 - filled)
    remaining = level.active_referrals - dashboard.active
    return text(
        language,
        "referral_progress_next",
        bar=bar,
        current=dashboard.active,
        target=level.active_referrals,
        remaining=remaining,
        days=level.reward_days,
        level=text(language, f"referral_level_{level.key}"),
    )

def _referral_levels(language: str, dashboard: ReferralDashboard) -> str:
    lines: list[str] = []
    for level in REFERRAL_LEVELS:
        marker = "✅" if level.key in dashboard.awarded_levels else (
            "👉" if dashboard.next_level and level.key == dashboard.next_level.key else "▫️"
        )
        badge = text(language, f"referral_badge_{level.badge}").strip() if level.badge else ""
        lines.append(
            text(
                language,
                "referral_level_row",
                marker=marker,
                target=level.active_referrals,
                days=level.reward_days,
                badge=badge,
                level=text(language, f"referral_level_{level.key}"),
            )
        )
    return "\n".join(lines)


@router.callback_query(F.data == "menu:referrals")
async def referrals_screen(
    callback: CallbackQuery,
    session_factory: async_sessionmaker,
    referral_service: ReferralService,
) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    language = user.language_code if user else "ru"
    dashboard = await referral_service.dashboard(callback.from_user.id)
    if dashboard is None:
        await callback.answer(text(language, "profile_missing"), show_alert=True)
        return
    link = await referral_service.referral_link(dashboard.code)
    content = text(
        language,
        "referral_screen",
        invited=dashboard.invited,
        active=dashboard.active,
        days=dashboard.earned_days,
        link=link,
        progress=_referral_progress(language, dashboard),
        levels=_referral_levels(language, dashboard),
    )
    await callback.message.edit_text(
        content,
        reply_markup=referrals_keyboard(language, dashboard.code),
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.inline_query(F.query.startswith("ref:"))
async def referral_inline_query(query: InlineQuery, referral_service: ReferralService) -> None:
    code = query.query.split(":", 1)[1].strip()
    try:
        invitation = await referral_service.inline_invitation(query.from_user.id, code)
    except Exception:
        log.exception("referral_inline_query_failed", telegram_id=query.from_user.id)
        invitation = None
    if invitation is None:
        language = query.from_user.language_code if query.from_user.language_code in LANGUAGES else "ru"
        unavailable = InlineQueryResultArticle(
            id="referral-unavailable",
            title=text(language, "referral_inline_unavailable_title"),
            description=text(language, "referral_inline_unavailable_description"),
            input_message_content=InputTextMessageContent(
                message_text=text(language, "referral_inline_unavailable_message"),
                parse_mode="HTML",
            ),
        )
        await query.answer([unavailable], cache_time=0, is_personal=True)
        return
    language, referral_link = invitation
    result = InlineQueryResultArticle(
        id=f"referral-{code}",
        title=text(language, "referral_inline_result_title"),
        description=text(language, "referral_inline_result_description"),
        input_message_content=InputTextMessageContent(
            message_text=text(language, "referral_inline_message"),
            parse_mode="HTML",
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=text(language, "btn_referral_start"),
                        url=referral_link,
                    )
                ]
            ]
        ),
    )
    await query.answer([result], cache_time=0, is_personal=True)


@router.callback_query(F.data.startswith("referral:leaderboard"))
async def referral_leaderboard(
    callback: CallbackQuery,
    session_factory: async_sessionmaker,
    referral_service: ReferralService,
) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    language = user.language_code if user else "ru"
    entries, outside = await referral_service.leaderboard(callback.from_user.id)
    try:
        requested_page = int(callback.data.rsplit(":", 1)[1]) if callback.data.count(":") == 2 else 0
    except ValueError:
        requested_page = 0
    page_size = 20
    pages = max(1, (len(entries) + page_size - 1) // page_size)
    page = min(max(0, requested_page), pages - 1)
    visible_entries = entries[page * page_size : (page + 1) * page_size]
    lines = [text(language, "referral_leaderboard_title")]
    if not entries:
        lines.append(text(language, "referral_leaderboard_empty"))
    else:
        for entry in visible_entries:
            badge = text(language, f"referral_badge_{entry.badge}") if entry.badge else ""
            lines.append(
                text(
                    language,
                    "referral_leaderboard_row",
                    position=entry.position,
                    name=escape(entry.display_name[:24]),
                    active=entry.active_referrals,
                    badge=badge,
                )
            )
    lines.append(text(language, "pagination", page=page + 1, pages=pages))
    if outside and page + 1 == pages:
        lines.extend(
            [
                text(language, "referral_leaderboard_your_position"),
                text(
                    language,
                    "referral_leaderboard_row",
                    position=outside.position,
                    name=escape(outside.display_name[:32]),
                    active=outside.active_referrals,
                    badge=text(language, f"referral_badge_{outside.badge}") if outside.badge else "",
                ),
            ]
        )
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=referral_leaderboard_keyboard(language, page, pages),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:search")
async def search_start(callback: CallbackQuery, state: FSMContext, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    if not user or not user.country_code:
        await callback.answer(text("ru", "start_first"), show_alert=True)
        return
    await state.set_state(AddGame.query)
    await state.update_data(panel_chat_id=callback.message.chat.id, panel_message_id=callback.message.message_id)
    await callback.message.edit_text(
        text(user.language_code, "search_prompt"), reply_markup=back_keyboard("home", user.language_code)
    )
    await callback.answer()


@router.message(AddGame.query)
async def search_game(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker,
    game_search: GameSearchService,
) -> None:
    async with session_factory() as session:
        user = await _user(session, message.from_user.id)
    try:
        games = await game_search.search(
            message.text or "", _steam_country(user.country_code), _steam_language(user.language_code)
        )
    except (SteamError, ValueError):
        games = []
    if not games:
        await _replace_panel(
            message,
            state,
            text(user.language_code, "not_found"),
            back_keyboard("home", user.language_code),
        )
        return
    await state.update_data(results={str(game.app_id): game.name for game in games})
    await state.set_state(AddGame.select)
    keyboard = games_keyboard(games, user.language_code)
    await _replace_panel(message, state, text(user.language_code, "choose_game"), keyboard)


@router.callback_query(AddGame.select, F.data.startswith("game:"))
async def select_game(
    callback: CallbackQuery, state: FSMContext, session_factory: async_sessionmaker, steam: SteamProvider
) -> None:
    app_id = int(callback.data.split(":", 1)[1])
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    try:
        game, price = await steam.details(
            app_id, _steam_country(user.country_code), _steam_language(user.language_code)
        )
    except SteamError:
        await callback.answer(text(user.language_code, "steam_unavailable"), show_alert=True)
        return
    await state.update_data(
        app_id=app_id,
        name=game.name,
        image=game.header_image,
        current_price=str(price.final) if price else None,
        current_currency=price.currency if price else None,
    )
    await state.set_state(AddGame.rule)
    normalized = _normalized(price)
    await callback.message.edit_text(
        format_price_card(game.name, normalized, user.language_code) + "\n\n" + text(user.language_code, "choose_rule"),
        parse_mode="HTML",
        reply_markup=rule_keyboard(user.language_code),
    )
    await callback.answer()


@router.callback_query(AddGame.rule, F.data.startswith("rule:"))
async def select_rule(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker,
    steam: SteamProvider,
    referral_service: ReferralService,
) -> None:
    rule = callback.data.split(":", 1)[1]
    await state.update_data(rule=rule)
    if rule == "any":
        await _save_watch(callback, state, session_factory, steam, referral_service, min_discount=1)
        return
    await state.set_state(AddGame.value)
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    if rule == "discount":
        prompt = text(user.language_code, "enter_discount")
    else:
        data = await state.get_data()
        prompt = (
            text(
                user.language_code,
                "enter_price_current",
                price=format_money(Decimal(data["current_price"]), data["current_currency"]),
            )
            if data.get("current_price") and data.get("current_currency")
            else text(user.language_code, "enter_price_unavailable")
        )
    await state.update_data(panel_chat_id=callback.message.chat.id, panel_message_id=callback.message.message_id)
    await callback.message.edit_text(prompt, reply_markup=back_keyboard("games", user.language_code))
    await callback.answer()


@router.callback_query(F.data == "search:results")
async def search_results(callback: CallbackQuery, state: FSMContext, session_factory: async_sessionmaker) -> None:
    data = await state.get_data()
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    language = user.language_code if user else "ru"
    games = [
        SteamGame(app_id=int(app_id), name=name, header_image=None, game_type="game", is_free=False)
        for app_id, name in data.get("results", {}).items()
    ]
    await state.set_state(AddGame.select)
    await callback.message.edit_text(
        text(language, "choose_game") if games else text(language, "not_found"),
        reply_markup=games_keyboard(games, language),
    )
    await callback.answer()


@router.message(AddGame.value)
async def rule_value(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker,
    steam: SteamProvider,
    referral_service: ReferralService,
) -> None:
    data = await state.get_data()
    try:
        if data["rule"] == "discount":
            value = int(message.text or "")
            if not 1 <= value <= 100:
                raise ValueError
            await _save_watch(message, state, session_factory, steam, referral_service, min_discount=value)
        else:
            value = Decimal((message.text or "").replace(",", "."))
            if value <= 0:
                raise ValueError
            await _save_watch(message, state, session_factory, steam, referral_service, max_price=value)
    except (ValueError, InvalidOperation):
        language = await _language(session_factory, message.from_user.id)
        await _replace_panel(message, state, text(language, "invalid_value"), back_keyboard("games", language))


async def _save_watch(
    event: CallbackQuery | Message,
    state: FSMContext,
    session_factory: async_sessionmaker,
    steam: SteamProvider,
    referral_service: ReferralService,
    min_discount: int | None = None,
    max_price: Decimal | None = None,
) -> None:
    tg_user = event.from_user
    data = await state.get_data()
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_id == tg_user.id).with_for_update())
        game = await get_or_create_game(session, data["app_id"], data["name"], data.get("image"))
        existing = await session.scalar(
            select(WatchRule).where(WatchRule.user_id == user.id, WatchRule.game_id == game.id)
        )
        limit = PREMIUM_GAME_LIMIT if user.is_premium else FREE_GAME_LIMIT
        if existing is None and await watch_count(session, user.id) >= limit:
            answer = event.message.answer if isinstance(event, CallbackQuery) else event.answer
            await answer(text(user.language_code, "limit_premium" if user.is_premium else "limit_free"))
            if isinstance(event, CallbackQuery):
                await event.answer()
            return
        if existing:
            existing.min_discount, existing.max_price = min_discount, max_price
            existing.target_currency = REGIONS[user.country_code].currency if max_price is not None else None
            existing.last_notification_fingerprint = None
            existing.condition_was_met = False
            rule = existing
        else:
            rule = WatchRule(
                user_id=user.id,
                game_id=game.id,
                min_discount=min_discount,
                max_price=max_price,
                target_currency=REGIONS[user.country_code].currency if max_price is not None else None,
            )
            session.add(rule)
        price = None
        try:
            refreshed_game, price = await steam.details(
                game.steam_app_id,
                _steam_country(user.country_code),
                _steam_language(user.language_code),
                force_refresh=True,
            )
            game.name, game.header_image = refreshed_game.name, refreshed_game.header_image
            if price is not None:
                if max_price is not None:
                    rule.target_currency = price.currency
                session.add(
                    PriceSnapshot(
                        game_id=game.id,
                        country_code=user.country_code,
                        currency=price.currency,
                        initial_price=price.initial,
                        final_price=price.final,
                        discount_percent=price.discount_percent,
                        checked_at=datetime.now(UTC),
                    )
                )
        except SteamError:
            price = None
        await session.commit()
        language = user.language_code
        rule_id, app_id, game_name = rule.id, game.steam_app_id, game.name
        user_id = user.id
    await referral_service.activate_if_eligible(user_id)
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(
            format_price_card(game_name, _normalized(price), language)
            + "\n\n"
            + text(language, "added" if price is not None else "price_retry"),
            parse_mode="HTML",
            reply_markup=watch_card_keyboard(rule_id, app_id, language, retry_price=price is None),
        )
    else:
        await _replace_panel(
            event,
            state,
            format_price_card(game_name, _normalized(price), language)
            + "\n\n"
            + text(language, "added" if price is not None else "price_retry"),
            watch_card_keyboard(rule_id, app_id, language, retry_price=price is None),
        )
    await state.clear()
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.callback_query(F.data == "menu:games")
async def my_games(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        rules = (
            await session.scalars(
                select(WatchRule).options(selectinload(WatchRule.game)).where(WatchRule.user_id == user.id)
            )
        ).all()
    if not rules:
        await callback.message.edit_text(
            text(user.language_code, "no_games"), reply_markup=watch_list_keyboard([], user.language_code)
        )
    else:
        await callback.message.edit_text(
            text(user.language_code, "my_games"), reply_markup=watch_list_keyboard(list(rules), user.language_code)
        )
    await callback.answer()


@router.callback_query(F.data.startswith("watch:"))
async def watch_card(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    rule_id = int(callback.data.split(":", 1)[1])
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        rule = await session.scalar(
            select(WatchRule)
            .options(selectinload(WatchRule.game))
            .where(WatchRule.id == rule_id, WatchRule.user_id == user.id)
        )
        if rule is None:
            await callback.answer(text(user.language_code, "game_missing"), show_alert=True)
            return
        latest = await session.scalar(
            select(PriceSnapshot)
            .where(PriceSnapshot.game_id == rule.game_id, PriceSnapshot.country_code == user.country_code)
            .order_by(PriceSnapshot.checked_at.desc())
            .limit(1)
        )
    await callback.message.edit_text(
        _watch_card_content(rule, latest, user),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=watch_card_keyboard(
            rule.id, rule.game.steam_app_id, user.language_code, rule.notifications_enabled
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("watch_edit_discount:"))
async def watch_edit_discount(callback: CallbackQuery, state: FSMContext, session_factory: async_sessionmaker) -> None:
    await callback.answer()
    rule_id = int(callback.data.split(":", 1)[1])
    language = await _language(session_factory, callback.from_user.id)
    if not await _owns_rule(session_factory, callback.from_user.id, rule_id):
        await callback.message.edit_text(
            text(language, "game_not_owned"), reply_markup=back_keyboard("games", language)
        )
        return
    await state.set_state(EditWatch.value)
    await state.update_data(
        edit_kind="discount",
        rule_id=rule_id,
        panel_chat_id=callback.message.chat.id,
        panel_message_id=callback.message.message_id,
    )
    await callback.message.edit_text(
        text(language, "enter_discount"),
        reply_markup=back_keyboard("games", language),
    )


@router.callback_query(F.data.startswith("watch_refresh:"))
async def watch_refresh(callback: CallbackQuery, session_factory: async_sessionmaker, steam: SteamProvider) -> None:
    await callback.answer()
    rule_id = int(callback.data.split(":", 1)[1])
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        rule = (
            await session.scalar(
                select(WatchRule)
                .options(selectinload(WatchRule.game))
                .where(WatchRule.id == rule_id, WatchRule.user_id == user.id)
            )
            if user
            else None
        )
        if rule is None:
            await callback.message.edit_text(text(user.language_code if user else "ru", "game_not_owned"))
            return
        try:
            _, price = await steam.details(
                rule.game.steam_app_id,
                _steam_country(user.country_code),
                _steam_language(user.language_code),
                force_refresh=True,
            )
        except SteamError:
            price = None
        if price is not None:
            session.add(
                PriceSnapshot(
                    game_id=rule.game_id,
                    country_code=user.country_code,
                    currency=price.currency,
                    initial_price=price.initial,
                    final_price=price.final,
                    discount_percent=price.discount_percent,
                    checked_at=datetime.now(UTC),
                )
            )
            await session.commit()
        content = _watch_card_content(
            rule,
            None
            if price is None
            else PriceSnapshot(
                game_id=rule.game_id,
                country_code=user.country_code,
                currency=price.currency,
                initial_price=price.initial,
                final_price=price.final,
                discount_percent=price.discount_percent,
                checked_at=datetime.now(UTC),
            ),
            user,
        )
    content += "\n\n" + text(user.language_code, "price_refreshed" if price else "price_refresh_failed")
    await callback.message.edit_text(
        content,
        parse_mode="HTML",
        reply_markup=watch_card_keyboard(
            rule.id, rule.game.steam_app_id, user.language_code, rule.notifications_enabled, retry_price=price is None
        ),
    )


@router.callback_query(F.data.startswith("watch_edit_price:"))
async def watch_edit_price(callback: CallbackQuery, state: FSMContext, session_factory: async_sessionmaker) -> None:
    await callback.answer()
    rule_id = int(callback.data.split(":", 1)[1])
    language = await _language(session_factory, callback.from_user.id)
    if not await _owns_rule(session_factory, callback.from_user.id, rule_id):
        await callback.message.edit_text(
            text(language, "game_not_owned"), reply_markup=back_keyboard("games", language)
        )
        return
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        rule = await session.scalar(select(WatchRule).where(WatchRule.id == rule_id, WatchRule.user_id == user.id))
        latest = await session.scalar(
            select(PriceSnapshot)
            .where(PriceSnapshot.game_id == rule.game_id, PriceSnapshot.country_code == user.country_code)
            .order_by(PriceSnapshot.checked_at.desc())
            .limit(1)
        )
    prompt = (
        text(language, "enter_price_current", price=format_money(latest.final_price, latest.currency))
        if latest
        else text(language, "enter_price_unavailable")
    )
    await state.set_state(EditWatch.value)
    await state.update_data(
        edit_kind="price",
        rule_id=rule_id,
        panel_chat_id=callback.message.chat.id,
        panel_message_id=callback.message.message_id,
    )
    await callback.message.edit_text(prompt, reply_markup=back_keyboard("games", language))


@router.message(EditWatch.value)
async def watch_edit_value(message: Message, state: FSMContext, session_factory: async_sessionmaker) -> None:
    data = await state.get_data()
    try:
        if data["edit_kind"] == "discount":
            value = int(message.text or "")
            if not 1 <= value <= 100:
                raise ValueError
            min_discount, max_price = value, None
        else:
            value = Decimal((message.text or "").replace(",", "."))
            if value <= 0:
                raise ValueError
            min_discount, max_price = None, value
    except (ValueError, InvalidOperation):
        language = await _language(session_factory, message.from_user.id)
        await _replace_panel(message, state, text(language, "invalid_value"), back_keyboard("games", language))
        return
    async with session_factory() as session:
        user = await _user(session, message.from_user.id)
        rule = await session.scalar(
            select(WatchRule)
            .options(selectinload(WatchRule.game))
            .where(WatchRule.id == data["rule_id"], WatchRule.user_id == user.id)
        )
        if rule is None:
            await _replace_panel(
                message, state, text(user.language_code, "game_missing"), back_keyboard("games", user.language_code)
            )
            await state.clear()
            return
        rule.min_discount, rule.max_price = min_discount, max_price
        rule.target_currency = None
        rule.last_notified_price = None
        rule.last_notification_fingerprint = None
        rule.condition_was_met = False
        await session.commit()
        latest = await session.scalar(
            select(PriceSnapshot)
            .where(
                PriceSnapshot.game_id == rule.game_id,
                PriceSnapshot.country_code == user.country_code,
            )
            .order_by(PriceSnapshot.checked_at.desc())
            .limit(1)
        )
        if max_price is not None and latest is not None:
            rule.target_currency = latest.currency
            await session.commit()
        content = _watch_card_content(rule, latest, user)
    await _replace_panel(
        message,
        state,
        content + "\n\n" + text(user.language_code, "rule_changed"),
        watch_card_keyboard(data["rule_id"], rule.game.steam_app_id, user.language_code, rule.notifications_enabled),
    )
    await state.clear()


@router.callback_query(F.data.startswith("watch_delete:"))
async def watch_delete(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    await callback.answer()
    rule_id = int(callback.data.split(":", 1)[1])
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        rule = await session.scalar(select(WatchRule).where(WatchRule.id == rule_id, WatchRule.user_id == user.id))
        if rule is None:
            await callback.message.edit_text(
                text(user.language_code, "game_missing"), reply_markup=back_keyboard("games", user.language_code)
            )
            return
        await session.delete(rule)
        await session.commit()
    await callback.message.edit_text(
        text(user.language_code, "game_deleted"), reply_markup=back_keyboard("games", user.language_code)
    )


@router.callback_query(F.data.startswith("watch_notify:"))
async def watch_notifications(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    await callback.answer()
    rule_id = int(callback.data.split(":", 1)[1])
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        rule = await session.scalar(
            select(WatchRule)
            .options(selectinload(WatchRule.game))
            .where(WatchRule.id == rule_id, WatchRule.user_id == user.id)
        )
        if rule is None:
            await callback.message.edit_text(
                text(user.language_code, "game_not_owned"),
                reply_markup=back_keyboard("games", user.language_code),
            )
            return
        rule.notifications_enabled = not rule.notifications_enabled
        await session.commit()
        latest = await session.scalar(
            select(PriceSnapshot)
            .where(
                PriceSnapshot.game_id == rule.game_id,
                PriceSnapshot.country_code == user.country_code,
            )
            .order_by(PriceSnapshot.checked_at.desc())
            .limit(1)
        )
        content = _watch_card_content(rule, latest, user)
    await callback.message.edit_text(
        content,
        reply_markup=watch_card_keyboard(
            rule.id, rule.game.steam_app_id, user.language_code, rule.notifications_enabled
        ),
    )


def _premium_filter_screen(rule: WatchRule, language: str) -> str:
    currency = rule.target_currency or "—"
    return text(
        language,
        "filter_screen",
        max_price=format_money(rule.max_price, currency) if rule.max_price is not None else "—",
        min_discount=f"{rule.min_discount}%" if rule.min_discount is not None else "—",
        drop_amount=(
            format_money(rule.minimum_price_drop_amount, currency)
            if rule.minimum_price_drop_amount is not None
            else "—"
        ),
        drop_percent=(f"{rule.minimum_price_drop_percent}%" if rule.minimum_price_drop_percent is not None else "—"),
    )


async def _premium_rule(session, telegram_id: int, rule_id: int) -> tuple[User | None, WatchRule | None]:
    user = await _user(session, telegram_id)
    rule = (
        await session.scalar(
            select(WatchRule)
            .options(selectinload(WatchRule.game))
            .where(WatchRule.id == rule_id, WatchRule.user_id == getattr(user, "id", 0))
        )
        if user
        else None
    )
    return user, rule


@router.callback_query(F.data.startswith("watch_filters:"))
async def watch_filters(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    rule_id = int(callback.data.rsplit(":", 1)[1])
    async with session_factory() as session:
        user, rule = await _premium_rule(session, callback.from_user.id, rule_id)
    language = user.language_code if user else "ru"
    if not user or not user.is_premium:
        await _show_premium_gate(callback, language, f"watch:{rule_id}", "filters")
        return
    if rule is None:
        await callback.answer(text(language, "game_missing"), show_alert=True)
        return
    await callback.message.edit_text(
        _premium_filter_screen(rule, language), reply_markup=premium_filters_keyboard(rule, language)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("filter_toggle:"))
async def filter_toggle(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    _, kind, raw_id = callback.data.split(":")
    async with session_factory() as session:
        user, rule = await _premium_rule(session, callback.from_user.id, int(raw_id))
        language = user.language_code if user else "ru"
        if not user or not user.is_premium or rule is None:
            await callback.answer(text(language, "premium_required"), show_alert=True)
            return
        attribute = {
            "new_low": "notify_on_new_historical_low",
            "known_low": "notify_on_known_historical_low",
            "any_drop": "notify_on_any_price_drop",
        }.get(kind)
        if attribute is None:
            await callback.answer(text(language, "invalid_value"), show_alert=True)
            return
        setattr(rule, attribute, not getattr(rule, attribute))
        rule.last_notification_fingerprint = None
        rule.last_notified_at = None
        rule.condition_was_met = False
        await session.commit()
    await callback.message.edit_text(
        _premium_filter_screen(rule, language), reply_markup=premium_filters_keyboard(rule, language)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("filter_repeat:"))
async def filter_repeat(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    rule_id = int(callback.data.rsplit(":", 1)[1])
    policies = ("on_change", "reentry", "once")
    async with session_factory() as session:
        user, rule = await _premium_rule(session, callback.from_user.id, rule_id)
        language = user.language_code if user else "ru"
        if not user or not user.is_premium or rule is None:
            await callback.answer(text(language, "premium_required"), show_alert=True)
            return
        current = rule.repeat_notification_policy if rule.repeat_notification_policy in policies else policies[0]
        rule.repeat_notification_policy = policies[(policies.index(current) + 1) % len(policies)]
        rule.last_notification_fingerprint = None
        rule.last_notified_at = None
        rule.condition_was_met = False
        await session.commit()
    await callback.message.edit_text(
        _premium_filter_screen(rule, language), reply_markup=premium_filters_keyboard(rule, language)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("filter_value:"))
async def filter_value_start(callback: CallbackQuery, state: FSMContext, session_factory: async_sessionmaker) -> None:
    _, kind, raw_id = callback.data.split(":")
    async with session_factory() as session:
        user, rule = await _premium_rule(session, callback.from_user.id, int(raw_id))
    language = user.language_code if user else "ru"
    if not user or not user.is_premium or rule is None:
        await callback.answer(text(language, "premium_required"), show_alert=True)
        return
    await state.set_state(PremiumFilterSetup.value)
    await state.set_data(
        {
            "filter_kind": kind,
            "rule_id": rule.id,
            "panel_chat_id": callback.message.chat.id,
            "panel_message_id": callback.message.message_id,
        }
    )
    await callback.message.edit_text(
        text(language, "filter_value_prompt", kind=text(language, f"filter_{kind}")),
        reply_markup=back_keyboard("games", language),
    )
    await callback.answer()


@router.message(PremiumFilterSetup.value)
async def filter_value_save(message: Message, state: FSMContext, session_factory: async_sessionmaker) -> None:
    data = await state.get_data()
    language = await _language(session_factory, message.from_user.id)
    try:
        value = Decimal((message.text or "").replace(",", "."))
        if value < 0:
            raise ValueError
        if data["filter_kind"] in {"min_discount", "drop_percent"} and (
            value != value.to_integral_value() or value > 100
        ):
            raise ValueError
    except (InvalidOperation, ValueError, KeyError):
        await _replace_panel(message, state, text(language, "invalid_value"), back_keyboard("games", language))
        return
    async with session_factory() as session:
        user, rule = await _premium_rule(session, message.from_user.id, int(data["rule_id"]))
        if not user or not user.is_premium or rule is None:
            await _replace_panel(message, state, text(language, "premium_required"), premium_keyboard(language))
            await state.clear()
            return
        kind = data["filter_kind"]
        if kind == "max_price":
            rule.max_price = value or None
            latest = await session.scalar(
                select(PriceSnapshot)
                .where(PriceSnapshot.game_id == rule.game_id, PriceSnapshot.country_code == user.country_code)
                .order_by(PriceSnapshot.checked_at.desc())
                .limit(1)
            )
            rule.target_currency = latest.currency if latest and value else None
        elif kind == "min_discount":
            rule.min_discount = int(value) or None
        elif kind == "drop_amount":
            rule.minimum_price_drop_amount = value or None
        elif kind == "drop_percent":
            rule.minimum_price_drop_percent = int(value) or None
        else:
            await state.clear()
            return
        rule.last_notification_fingerprint = None
        rule.last_notified_at = None
        rule.condition_was_met = False
        await session.commit()
    await _replace_panel(
        message,
        state,
        _premium_filter_screen(rule, language),
        premium_filters_keyboard(rule, language),
    )
    await state.clear()


@router.callback_query(F.data == "menu:settings")
async def settings_view(
    callback: CallbackQuery, state: FSMContext, session_factory: async_sessionmaker, settings: Settings
) -> None:
    await callback.answer()
    await state.clear()
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    if user is None:
        await _safe_edit_callback(
            callback,
            text("ru", "profile_missing"),
            back_keyboard("home"),
        )
        return
    content, markup = _profile_screen(user, settings.app_timezone)
    await _safe_edit_callback(callback, content, markup)


@router.callback_query(F.data == "settings:language")
async def settings_language(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    keyboard = language_keyboard()
    keyboard.inline_keyboard.append(back_keyboard("settings", language).inline_keyboard[0])
    await callback.message.edit_text(text(language, "settings_choose_language"), reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("lang:"))
async def change_language(callback: CallbackQuery, session_factory: async_sessionmaker, settings: Settings) -> None:
    language = callback.data.split(":", 1)[1]
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if user is None:
            await callback.answer(text(language, "start_first"), show_alert=True)
            return
        user.language_code = language
        await session.commit()
    content, markup = _profile_screen(user, settings.app_timezone)
    await callback.message.edit_text(content, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data.startswith("region:"))
async def change_region(
    callback: CallbackQuery,
    session_factory: async_sessionmaker,
    steam: SteamProvider,
    currency: CurrencyService,
    settings: Settings,
) -> None:
    country = callback.data.split(":", 1)[1]
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if user is None:
            await callback.answer(text("ru", "start_first"), show_alert=True)
            return
        if country not in REGIONS:
            await callback.answer(text(user.language_code, "invalid_value"), show_alert=True)
            return
        old_currency = REGIONS[user.country_code].currency
        new_currency = REGIONS[country].currency
        try:
            converted = await convert_user_money_conditions(
                session,
                currency,
                user.id,
                old_currency,
                new_currency,
            )
        except CurrencyError:
            await callback.answer(text(user.language_code, "condition_conversion_failed"), show_alert=True)
            return
        user.country_code = country
        await session.commit()
    await callback.answer(text(user.language_code, "price_refreshing"))
    await _refresh_user_prices(session_factory, steam, user.id, country, user.language_code)
    content, markup = _profile_screen(user, settings.app_timezone)
    content += "\n\n" + text(
        user.language_code,
        "region_changed",
        region=text(user.language_code, f"region_{country.lower()}"),
    )
    if converted:
        content += "\n" + text(user.language_code, "conditions_converted", currency=new_currency)
    await callback.message.edit_text(content, reply_markup=markup)


@router.callback_query(F.data == "settings:region")
async def settings_region(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    await callback.message.edit_text(
        text(language, "choose_region_group"), reply_markup=region_groups_keyboard(language, back=True)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("region_group:"))
async def settings_region_group(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    group = callback.data.split(":", 1)[1]
    if group not in {region.geo_group for region in REGIONS.values()}:
        await callback.answer(text(language, "invalid_value"), show_alert=True)
        return
    await callback.message.edit_text(
        text(language, "choose_region"), reply_markup=region_keyboard(language, group=group)
    )
    await callback.answer()


@router.callback_query(F.data == "region_groups")
async def settings_region_groups(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    await callback.message.edit_text(
        text(language, "choose_region_group"), reply_markup=region_groups_keyboard(language, back=True)
    )
    await callback.answer()


@router.callback_query(F.data == "settings:timezone")
async def settings_timezone(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    await callback.message.edit_text(
        text(language, "choose_timezone_group"), reply_markup=timezone_groups_keyboard(language)
    )
    await callback.answer()


@router.callback_query(F.data == "timezone:manual")
async def settings_manual_timezone(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker,
) -> None:
    language = await _language(session_factory, callback.from_user.id)
    await state.set_state(TimezoneSetup.manual)
    await state.update_data(
        timezone_prompt_chat_id=callback.message.chat.id,
        timezone_prompt_message_id=callback.message.message_id,
        language=language,
    )
    await callback.message.edit_text(
        text(language, "timezone_manual_prompt"),
        reply_markup=manual_timezone_keyboard(language),
    )
    await callback.answer()


@router.message(Onboarding.manual_timezone)
@router.message(TimezoneSetup.manual)
async def save_manual_timezone(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker,
    settings: Settings,
    referral_service: ReferralService,
) -> None:
    data = await state.get_data()
    language = data.get("language") or await _language(session_factory, message.from_user.id)
    prompt_chat_id = data.get("timezone_prompt_chat_id", message.chat.id)
    prompt_message_id = data.get("timezone_prompt_message_id")
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    normalized = normalize_utc_offset(message.text or "")
    if normalized is None:
        markup = manual_timezone_keyboard(
            language,
            onboarding=await state.get_state() == Onboarding.manual_timezone.state,
        )
        if prompt_message_id:
            try:
                await message.bot.edit_message_text(
                    text(language, "timezone_manual_invalid"),
                    chat_id=prompt_chat_id,
                    message_id=prompt_message_id,
                    reply_markup=markup,
                )
                return
            except TelegramBadRequest as error:
                if "message is not modified" in str(error).lower():
                    return
        sent = await message.bot.send_message(
            prompt_chat_id,
            text(language, "timezone_manual_invalid"),
            reply_markup=markup,
        )
        await state.update_data(timezone_prompt_chat_id=sent.chat.id, timezone_prompt_message_id=sent.message_id)
        return
    if await state.get_state() == Onboarding.manual_timezone.state:
        country = data.get("country")
        if country not in REGIONS:
            await state.clear()
            await message.answer(text(language, "invalid_value"))
            return
        async with session_factory() as session:
            user = await get_or_create_user(
                session,
                message.from_user.id,
                message.from_user.username,
                message.from_user.full_name,
            )
            user.language_code = language
            user.country_code = country
            user.timezone = normalized
            await referral_service.mark_onboarding_completed(session, user)
            await session.commit()
        await state.clear()
        content = text(
            language,
            "ready_with_timezone",
            region=text(language, f"region_{country.lower()}"),
            timezone=normalized,
        )
        if prompt_message_id:
            await message.bot.edit_message_text(
                content,
                chat_id=prompt_chat_id,
                message_id=prompt_message_id,
                reply_markup=main_keyboard(language),
            )
        else:
            await message.bot.send_message(prompt_chat_id, content, reply_markup=main_keyboard(language))
        return
    async with session_factory() as session:
        user = await _user(session, message.from_user.id)
        if user is None:
            await state.clear()
            await message.answer(text(language, "profile_missing"))
            return
        user.timezone = normalized
        await session.commit()
    await state.clear()
    content, markup = _profile_screen(user, settings.app_timezone)
    content += "\n\n" + text(language, "timezone_changed", timezone=normalized)
    if prompt_message_id:
        await message.bot.edit_message_text(
            content,
            chat_id=prompt_chat_id,
            message_id=prompt_message_id,
            reply_markup=markup,
        )
    else:
        await message.bot.send_message(prompt_chat_id, content, reply_markup=markup)


@router.callback_query(F.data.startswith("timezone_group:"))
async def settings_timezone_group(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    group = callback.data.split(":", 1)[1]
    if group not in {region.geo_group for region in REGIONS.values()}:
        await callback.answer(text(language, "invalid_value"), show_alert=True)
        return
    await callback.message.edit_text(text(language, "choose_timezone"), reply_markup=timezone_keyboard(language, group))
    await callback.answer()


@router.callback_query(F.data == "timezone_groups")
async def settings_timezone_groups(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    await callback.message.edit_text(
        text(language, "choose_timezone_group"), reply_markup=timezone_groups_keyboard(language)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("timezone:"))
async def change_timezone(callback: CallbackQuery, session_factory: async_sessionmaker, settings: Settings) -> None:
    timezone_name = callback.data.split(":", 1)[1]
    language = await _language(session_factory, callback.from_user.id)
    if timezone_name not in {region.timezone for region in REGIONS.values()}:
        await callback.answer(text(language, "invalid_value"), show_alert=True)
        return
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        user.timezone = timezone_name
        await session.commit()
    content, markup = _profile_screen(user, settings.app_timezone)
    await callback.message.edit_text(
        content + "\n\n" + text(user.language_code, "timezone_changed", timezone=timezone_name), reply_markup=markup
    )
    await callback.answer()


@router.callback_query(F.data == "menu:info")
async def information(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    await _safe_edit_callback(callback, text(language, "info_title"), info_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "settings:comparison_currency")
async def information_currency(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    language = user.language_code if user and user.language_code else "ru"
    current = user.comparison_currency if user else "USD"
    await _safe_edit_callback(
        callback,
        text(language, "info_currency_choose", currency=current),
        profile_currency_keyboard(language, current),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_currency:"))
async def information_currency_save(
    callback: CallbackQuery,
    session_factory: async_sessionmaker,
    currency: CurrencyService,
) -> None:
    currency_code = callback.data.split(":", 1)[1]
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        language = user.language_code if user and user.language_code else "ru"
        if not user or currency_code not in COMPARISON_CURRENCIES:
            await callback.answer(text(language, "invalid_value"), show_alert=True)
            return
        source_default = REGIONS[user.country_code].currency
        try:
            converted = await convert_user_money_conditions(
                session,
                currency,
                user.id,
                source_default,
                currency_code,
            )
        except CurrencyError:
            await callback.answer(text(language, "condition_conversion_failed"), show_alert=True)
            return
        user.comparison_currency = currency_code
        await session.commit()
    await _safe_edit_callback(
        callback,
        text(language, "info_region_page"),
        info_page_keyboard(language, "region"),
    )
    confirmation = text(language, "info_currency_saved", currency=currency_code)
    if converted:
        confirmation += "\n" + text(language, "conditions_converted", currency=currency_code)
    await callback.answer(confirmation)


@router.callback_query(F.data.startswith("info:"))
async def information_page(
    callback: CallbackQuery,
    session_factory: async_sessionmaker,
    settings: Settings,
) -> None:
    parts = callback.data.split(":")
    page = parts[1] if len(parts) > 1 else ""
    origin = parts[2] if len(parts) > 2 else ""
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    language = user.language_code if user and user.language_code else "ru"
    key = {
        "search": "info_search_page",
        "discounts": "info_discounts_page",
        "giveaways": "info_giveaways_page",
        "analytics": "info_analytics_page",
        "premium": "info_premium_page",
        "region": "info_region_page",
        "referrals": "info_referrals_page",
        "start": "info_start_page",
        "plans": "info_plans_page",
    }.get(page)
    if key is None:
        await callback.answer(text(language, "ui_error"), show_alert=True)
        return

    values: dict[str, object] = {}
    if page in {"premium", "plans"}:
        values = {
            "free_limit": FREE_GAME_LIMIT,
            "premium_limit": PREMIUM_GAME_LIMIT,
            "free_frequency": _info_frequency(language, settings.free_check_hours),
            "premium_frequency": _info_frequency(language, settings.premium_check_hours),
            "plans": "\n".join(
                f"• {text(language, 'premium_period', months=months, stars=stars)}"
                for months, stars in PREMIUM_PRICES.items()
            ),
            "subscription_status": text(
                language,
                "info_subscription_active" if user and user.is_premium else "info_subscription_free",
            ),
        }
    back_callback = f"info:{origin}" if origin in {"discounts", "giveaways", "analytics"} else "menu:info"
    await _safe_edit_callback(
        callback,
        text(language, key, **values),
        info_page_keyboard(
            language,
            page,
            is_premium=bool(user and user.is_premium),
            is_admin=callback.from_user.id in settings.admin_ids,
            premium_back=back_callback,
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "settings:quiet")
async def settings_quiet(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    if not user or not user.is_premium:
        await _show_premium_gate(callback, user.language_code if user else "ru", "menu:settings", "quiet")
        return
    start = user.quiet_hours_start or time(23)
    end = user.quiet_hours_end or time(8)
    content = text(
        user.language_code,
        "quiet_screen",
        status=text(user.language_code, "enabled_title" if user.quiet_hours_enabled else "disabled_title"),
        start=start.strftime("%H:%M"),
        end=end.strftime("%H:%M"),
        timezone=user.timezone,
    )
    await callback.message.edit_text(
        content, reply_markup=quiet_hours_keyboard(user.language_code, user.quiet_hours_enabled)
    )
    await callback.answer()


@router.callback_query(F.data == "quiet:toggle")
async def quiet_toggle(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if not user or not user.is_premium:
            await callback.answer(text(user.language_code if user else "ru", "premium_required"), show_alert=True)
            return
        user.quiet_hours_enabled = not user.quiet_hours_enabled
        user.quiet_hours_start = user.quiet_hours_start or time(23)
        user.quiet_hours_end = user.quiet_hours_end or time(8)
        await session.commit()
        enabled = user.quiet_hours_enabled
        language = user.language_code
        content = text(
            language,
            "quiet_screen",
            status=text(language, "enabled_title" if enabled else "disabled_title"),
            start=user.quiet_hours_start.strftime("%H:%M"),
            end=user.quiet_hours_end.strftime("%H:%M"),
            timezone=user.timezone,
        )
    if not enabled:
        content += "\n\n" + text(language, "quiet_queue_kept")
    await callback.message.edit_text(content, reply_markup=quiet_hours_keyboard(language, enabled))
    await callback.answer()


@router.callback_query(F.data.in_({"quiet:start", "quiet:end"}))
async def quiet_choose_hour(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    prefix = callback.data.replace(":", "_")
    await callback.message.edit_text(text(language, "choose_hour"), reply_markup=hour_keyboard(language, prefix))
    await callback.answer()


@router.callback_query(F.data.startswith("quiet_start_hour:") | F.data.startswith("quiet_end_hour:"))
async def quiet_choose_minute(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    prefix, hour = callback.data.split(":")
    prefix = prefix.removesuffix("_hour")
    await callback.message.edit_text(
        text(language, "choose_minute"), reply_markup=minute_keyboard(language, prefix, int(hour))
    )
    await callback.answer()


@router.callback_query(F.data.startswith("quiet_start_minute:") | F.data.startswith("quiet_end_minute:"))
async def quiet_save_time(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    prefix, hour, minute = callback.data.split(":")
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if not user or not user.is_premium:
            await callback.answer(text(user.language_code if user else "ru", "premium_required"), show_alert=True)
            return
        value = time(int(hour), int(minute))
        if "start" in prefix:
            user.quiet_hours_start = value
        else:
            user.quiet_hours_end = value
        await session.commit()
    await settings_quiet(callback, session_factory)


@router.callback_query(F.data == "settings:digest")
async def settings_digest(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    if not user or not user.is_premium:
        await _show_premium_gate(callback, user.language_code if user else "ru", "menu:settings", "digest")
        return
    await _safe_edit_callback(
        callback,
        _digest_screen(user),
        digest_keyboard(user.language_code, user.daily_digest_enabled, user.weekly_digest_enabled),
    )
    await callback.answer()


@router.callback_query(F.data.in_({"digest:daily", "digest:weekly"}))
async def digest_kind(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    kind = callback.data.split(":", 1)[1]
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    if not user or not user.is_premium:
        await callback.answer(text(user.language_code if user else "ru", "premium_required"), show_alert=True)
        return
    await _render_digest_kind(callback, user, kind)
    await callback.answer()


async def _render_digest_kind(callback: CallbackQuery, user: User, kind: str) -> None:
    enabled = user.daily_digest_enabled if kind == "daily" else user.weekly_digest_enabled
    await _safe_edit_callback(
        callback,
        _digest_kind_screen(user, kind),
        digest_kind_keyboard(user.language_code, kind, enabled),
    )


@router.callback_query(F.data.startswith("digest_toggle:"))
async def digest_toggle(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    kind = callback.data.split(":", 1)[1]
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if not user or not user.is_premium:
            await callback.answer(text(user.language_code if user else "ru", "premium_required"), show_alert=True)
            return
        if kind == "daily":
            user.daily_digest_enabled = not user.daily_digest_enabled
            user.daily_digest_time = user.daily_digest_time or time(20)
        else:
            user.weekly_digest_enabled = not user.weekly_digest_enabled
            user.weekly_digest_time = user.weekly_digest_time or time(18)
        await session.commit()
    await _render_digest_kind(callback, user, kind)
    await callback.answer(text(user.language_code, "digest_saved"))


@router.callback_query(F.data == "digest:disable_all")
async def digest_disable_all(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if not user or not user.is_premium:
            await callback.answer(text(user.language_code if user else "ru", "premium_required"), show_alert=True)
            return
        user.daily_digest_enabled = user.weekly_digest_enabled = False
        await session.commit()
    await _safe_edit_callback(
        callback,
        _digest_screen(user),
        digest_keyboard(user.language_code, False, False),
    )
    await callback.answer(text(user.language_code, "digest_saved"))


@router.callback_query(F.data == "digest:guide")
async def digest_guide(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    await _safe_edit_callback(
        callback,
        text(language, "digest_guide"),
        InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=text(language, "btn_back"), callback_data="settings:digest")]]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("digest_time:"))
async def digest_time_start(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    if not user or not user.is_premium:
        await callback.answer(text(user.language_code if user else "ru", "premium_required"), show_alert=True)
        return
    language = user.language_code
    kind = callback.data.split(":", 1)[1]
    await callback.message.edit_text(
        text(language, "choose_hour"),
        reply_markup=hour_keyboard(language, f"digest_{kind}", f"digest:{kind}"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("digest_daily_hour:") | F.data.startswith("digest_weekly_hour:"))
async def digest_time_minute(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    if not user or not user.is_premium:
        await callback.answer(text(user.language_code if user else "ru", "premium_required"), show_alert=True)
        return
    language = user.language_code
    prefix, hour = callback.data.split(":")
    prefix = prefix.removesuffix("_hour")
    await callback.message.edit_text(
        text(language, "choose_minute"),
        reply_markup=minute_keyboard(language, prefix, int(hour), f"digest:{prefix.removeprefix('digest_')}"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("digest_daily_minute:") | F.data.startswith("digest_weekly_minute:"))
async def digest_time_save(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    prefix, hour, minute = callback.data.split(":")
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if not user or not user.is_premium:
            await callback.answer(text(user.language_code if user else "ru", "premium_required"), show_alert=True)
            return
        value = time(int(hour), int(minute))
        if "daily" in prefix:
            user.daily_digest_time, user.daily_digest_enabled = value, True
        else:
            user.weekly_digest_time, user.weekly_digest_enabled = value, True
        await session.commit()
    kind = "daily" if "daily" in prefix else "weekly"
    await _render_digest_kind(callback, user, kind)
    await callback.answer(text(user.language_code, "digest_saved"))


@router.callback_query(F.data == "digest:weekday")
async def digest_weekday_menu(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    if not user or not user.is_premium:
        await callback.answer(text(user.language_code if user else "ru", "premium_required"), show_alert=True)
        return
    language = user.language_code
    await callback.message.edit_text(text(language, "choose_weekday"), reply_markup=weekday_keyboard(language))
    await callback.answer()


@router.callback_query(F.data.startswith("digest_weekday:"))
async def digest_weekday_save(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    weekday = int(callback.data.split(":", 1)[1])
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if not user or not user.is_premium:
            await callback.answer(text(user.language_code if user else "ru", "premium_required"), show_alert=True)
            return
        user.weekly_digest_weekday = weekday
        await session.commit()
    await _render_digest_kind(callback, user, "weekly")
    await callback.answer(text(user.language_code, "digest_saved"))


@router.callback_query(F.data == "menu:premium")
async def premium(callback: CallbackQuery, session_factory: async_sessionmaker, settings: Settings) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        tracked = await watch_count(session, user.id) if user else 0
    content, keyboard = _premium_screen(
        user,
        user.language_code,
        settings.telegram_payment_test_mode,
        tracked,
        is_admin=callback.from_user.id in settings.admin_ids,
    )
    await callback.message.edit_text(content, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("premium:info"))
async def premium_info(
    callback: CallbackQuery,
    session_factory: async_sessionmaker,
    settings: Settings,
) -> None:
    language = await _language(session_factory, callback.from_user.id)
    parts = callback.data.split(":", 2)
    back_callback = parts[2] if len(parts) == 3 and parts[2] else "menu:premium"
    await _safe_edit_callback(
        callback,
        text(language, "premium_full_info"),
        premium_info_return_keyboard(
            language,
            back_callback,
            is_admin=callback.from_user.id in settings.admin_ids,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buy:"))
async def buy_premium(
    callback: CallbackQuery,
    bot: Bot,
    session_factory: async_sessionmaker,
    settings: Settings,
) -> None:
    try:
        _, tariff_code, raw_stars = callback.data.split(":")
        stars = int(raw_stars)
    except (ValueError, AttributeError):
        await callback.answer(text("ru", "payment_invalid"), show_alert=True)
        return
    language = await _language(session_factory, callback.from_user.id)
    purchase = _parse_premium_payload(
        f"premium:{tariff_code}",
        stars,
        allow_admin_test=callback.from_user.id in settings.admin_ids,
    )
    if purchase is None:
        await callback.answer(text(language, "payment_invalid"), show_alert=True)
        return
    title = (
        text(language, "premium_invoice_title_days", days=purchase.days)
        if purchase.months == 0
        else text(language, "premium_invoice_title", months=purchase.months)
    )
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=title,
        description=text(language, "premium_invoice_description"),
        payload=f"premium:{tariff_code}",
        currency="XTR",
        prices=[LabeledPrice(label="Premium", amount=stars)],
        reply_markup=invoice_keyboard(language, stars),
    )
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(
    query: PreCheckoutQuery,
    session_factory: async_sessionmaker,
    settings: Settings,
) -> None:
    valid = (
        query.currency == "XTR"
        and _parse_premium_payload(
            query.invoice_payload,
            query.total_amount,
            allow_admin_test=query.from_user.id in settings.admin_ids,
        )
        is not None
    )
    language = await _language(session_factory, query.from_user.id)
    await query.answer(ok=valid, error_message=None if valid else text(language, "payment_invalid"))


@router.message(F.successful_payment)
async def payment_success(message: Message, session_factory: async_sessionmaker, settings: Settings) -> None:
    payment = message.successful_payment
    purchase = _parse_premium_payload(
        payment.invoice_payload,
        payment.total_amount,
        allow_admin_test=message.from_user.id in settings.admin_ids,
    )
    if payment.currency != "XTR" or purchase is None:
        await message.answer(text(await _language(session_factory, message.from_user.id), "payment_rejected"))
        return
    async with session_factory() as session:
        user = await _user(session, message.from_user.id)
        if user is None:
            await message.answer(text("ru", "profile_missing"))
            return
        inserted = await session.scalar(
            pg_insert(Payment)
            .values(
                user_id=user.id,
                telegram_charge_id=payment.telegram_payment_charge_id,
                amount_stars=payment.total_amount,
                months=purchase.months,
                duration_days=purchase.days,
                tariff="admin_test_day" if purchase.months == 0 else f"premium_{purchase.months}m",
                status="successful",
                source="telegram_stars_test" if settings.telegram_payment_test_mode else "telegram_stars",
                username=user.username,
                display_name=user.display_name,
                paid_at=datetime.now(UTC),
                is_test=settings.telegram_payment_test_mode,
            )
            .on_conflict_do_nothing(index_elements=[Payment.telegram_charge_id])
            .returning(Payment.id)
        )
        if inserted is None:
            await session.rollback()
            await message.answer(text(user.language_code, "payment_duplicate"))
            return
        base = user.premium_until if user.is_premium else datetime.now(UTC)
        user.plan = Plan.PREMIUM
        user.premium_source = "telegram_stars_test" if settings.telegram_payment_test_mode else "telegram_stars"
        if not user.is_premium:
            user.premium_started_at = datetime.now(UTC)
        user.premium_expired_notified_at = None
        user.premium_until = base + timedelta(days=purchase.days)
        await session.commit()
    await message.answer(
        text(
            user.language_code,
            "payment_success_days" if purchase.months == 0 else "payment_success",
            months=purchase.months,
            days=purchase.days,
        ),
        reply_markup=dismiss_keyboard(user.language_code),
    )


@router.callback_query(F.data == "premium:history")
async def premium_history(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        payments = list(
            (
                await session.scalars(
                    select(Payment).where(Payment.user_id == user.id).order_by(Payment.paid_at.desc()).limit(20)
                )
            ).all()
        )
    language = user.language_code
    if payments:
        items = "\n".join(
            text(
                language,
                "payment_history_item_days" if p.months == 0 else "payment_history_item",
                date=p.paid_at.strftime("%d.%m.%Y"),
                stars=p.amount_stars,
                months=p.months,
                days=ADMIN_TEST_PREMIUM_DAYS,
                test=text(language, "test_suffix") if p.is_test else "",
            )
            for p in payments
        )
        content = text(language, "payment_history", items=items)
    else:
        content = text(language, "payment_history_empty")
    await callback.message.edit_text(content, reply_markup=back_keyboard("premium", language))
    await callback.answer()


@router.callback_query(F.data == "premium:manage")
async def premium_manage(callback: CallbackQuery, session_factory: async_sessionmaker, settings: Settings) -> None:
    language = await _language(session_factory, callback.from_user.id)
    content = text(language, "premium")
    if settings.telegram_payment_test_mode:
        content += "\n\n" + text(language, "premium_test_label")
    await callback.message.edit_text(
        content,
        reply_markup=premium_keyboard(language, is_admin=callback.from_user.id in settings.admin_ids),
    )
    await callback.answer()


async def _premium_rules(session_factory: async_sessionmaker, telegram_id: int) -> tuple[User | None, list[WatchRule]]:
    async with session_factory() as session:
        user = await _user(session, telegram_id)
        if not user or not user.is_premium:
            return user, []
        rules = list(
            (
                await session.scalars(
                    select(WatchRule)
                    .options(selectinload(WatchRule.game))
                    .where(WatchRule.user_id == user.id, WatchRule.enabled.is_(True))
                    .order_by(WatchRule.created_at)
                )
            ).all()
        )
    return user, rules


@router.callback_query(F.data.in_({"premium:analytics", "premium:compare"}))
async def premium_tool_games(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    user, rules = await _premium_rules(session_factory, callback.from_user.id)
    language = user.language_code if user else "ru"
    if not user or not user.is_premium:
        await _show_premium_gate(callback, language, "menu:premium")
        return
    action = "analytics" if callback.data.endswith("analytics") else "compare"
    content = text(language, f"premium_{action}_choose")
    if not rules:
        content += "\n\n" + text(language, "games_empty")
    await callback.message.edit_text(content, reply_markup=premium_games_keyboard(rules, action, language))
    await callback.answer()


@router.callback_query(F.data.startswith("premium_analytics:"))
@router.callback_query(F.data.startswith("analytics_refresh:"))
@router.callback_query(F.data.startswith("analytics_details:"))
@router.callback_query(F.data.startswith("analytics_summary:"))
@router.callback_query(F.data.startswith("deals_analytics:"))
async def premium_game_analytics(
    callback: CallbackQuery,
    session_factory: async_sessionmaker,
    redis: Redis,
    steam: SteamProvider,
    historical_lows: HistoricalLowSync | None,
    price_history: PriceHistoryService,
    price_analytics: PriceAnalyticsService,
    currency: CurrencyService,
) -> None:
    rule_id = int(callback.data.rsplit(":", 1)[1])
    refresh = callback.data.startswith("analytics_refresh:")
    if callback.data.startswith("deals_analytics:"):
        await redis.set(f"analytics:origin:{callback.from_user.id}", "deals:return", ex=3600)
    elif callback.data.startswith("premium_analytics:"):
        await redis.delete(f"analytics:origin:{callback.from_user.id}")
    if refresh and not await redis.set(f"analytics:refresh:{callback.from_user.id}:{rule_id}", "1", ex=60, nx=True):
        language = await _language(session_factory, callback.from_user.id)
        await callback.answer(text(language, "analytics_refresh_limited"), show_alert=True)
        return
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        language = user.language_code if user else "ru"
        rule = await session.scalar(
            select(WatchRule)
            .options(selectinload(WatchRule.game))
            .where(WatchRule.id == rule_id, WatchRule.user_id == getattr(user, "id", 0))
        )
        if not user or not rule:
            await callback.answer(text(language, "game_missing"), show_alert=True)
            return
        if not user.is_premium:
            back_callback = f"watch:{rule_id}"
            if callback.data.startswith("deals_analytics:"):
                back_callback = "deals:return"
            await _show_premium_gate(callback, language, back_callback)
            return
        country = user.country_code
    if refresh:
        await callback.answer(text(language, "analytics_refresh_started"))
        await callback.message.edit_text(text(language, "analytics_loading"))
        try:
            _, price = await steam.details(
                rule.game.steam_app_id,
                REGIONS[country].steam_country_code,
                _steam_language(language),
                force_refresh=True,
            )
            if price is not None:
                checked_at = datetime.now(UTC)
                async with session_factory() as session:
                    db_rule = await session.get(WatchRule, rule.id)
                    db_rule.last_checked_at = checked_at
                    session.add(
                        PriceSnapshot(
                            game_id=rule.game_id,
                            country_code=country,
                            currency=price.currency,
                            initial_price=price.initial,
                            final_price=price.final,
                            discount_percent=price.discount_percent,
                            checked_at=checked_at,
                        )
                    )
                    await session.commit()
            if historical_lows:
                await historical_lows.sync_game(rule.game_id, rule.game.steam_app_id, country)
        except (SteamError, ITADError):
            log.warning(
                "analytics_refresh_partial_failure",
                rule_id=rule.id,
                steam_app_id=rule.game.steam_app_id,
                exc_info=True,
            )
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        snapshots = list(
            (
                await session.scalars(
                    select(PriceSnapshot)
                    .where(PriceSnapshot.game_id == rule.game_id, PriceSnapshot.country_code == country)
                    .order_by(PriceSnapshot.checked_at.desc())
                    .limit(100)
                )
            ).all()
        )
    snapshots.reverse()
    if not snapshots:
        await _safe_edit_callback(
            callback,
            text(language, "premium_history_insufficient"),
            back_keyboard("games", language),
        )
        if not refresh:
            await callback.answer()
        return
    latest = snapshots[-1]
    analysis_target = rule.max_price
    if analysis_target is not None and rule.target_currency not in {None, latest.currency}:
        try:
            analysis_target, _ = await currency.convert(
                analysis_target,
                rule.target_currency,
                latest.currency,
            )
        except CurrencyError:
            analysis_target = None
    history_result = await price_history.get_history(
        rule.game_id,
        rule.game.steam_app_id,
        country,
        latest.currency,
        None,
    )
    analysis = price_analytics.analyze(
        history_result.points,
        latest.final_price,
        latest.initial_price,
        latest.discount_percent,
        analysis_target,
        rule.min_discount,
    )
    content = price_analytics.build_card(
        language,
        rule.game.name,
        latest.currency,
        analysis,
        analysis_target,
        rule.min_discount,
    )
    origin = await redis.get(f"analytics:origin:{callback.from_user.id}")
    await _safe_edit_callback(
        callback,
        content,
        analytics_keyboard(
            language,
            rule.id,
            rule.game.steam_app_id,
            back_callback=origin or f"watch:{rule.id}",
        ),
    )
    if not refresh:
        await callback.answer()


@router.callback_query(F.data.startswith("price_history:"))
async def premium_price_history(
    callback: CallbackQuery,
    session_factory: async_sessionmaker,
    redis: Redis,
    price_history: PriceHistoryService,
    price_analytics: PriceAnalyticsService,
    price_charts: PriceChartService,
    currency: CurrencyService,
) -> None:
    try:
        _, rule_value, period_value = (callback.data or "").split(":", 2)
        rule_id = int(rule_value)
        period_days = None if period_value == "all" else int(period_value)
        if period_days not in {None, 7, 30, 90, 365}:
            raise ValueError
    except (TypeError, ValueError):
        await callback.answer(text("ru", "stale_callback"), show_alert=True)
        return

    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        language = user.language_code if user else "ru"
        rule = await session.scalar(
            select(WatchRule)
            .options(selectinload(WatchRule.game))
            .where(WatchRule.id == rule_id, WatchRule.user_id == getattr(user, "id", 0))
        )
        if user is None or rule is None:
            await callback.answer(text(language, "game_missing"), show_alert=True)
            return
        if not user.is_premium:
            await _show_premium_gate(callback, language, f"watch:{rule_id}")
            return
        latest = await session.scalar(
            select(PriceSnapshot)
            .where(PriceSnapshot.game_id == rule.game_id, PriceSnapshot.country_code == user.country_code)
            .order_by(PriceSnapshot.checked_at.desc())
            .limit(1)
        )

    if latest is None:
        await callback.answer(text(language, "premium_history_insufficient"), show_alert=True)
        return
    await callback.answer()
    message_type = "photo" if isinstance(callback.message, Message) and callback.message.photo else "text"
    try:
        if isinstance(callback.message, Message) and callback.message.photo:
            await callback.message.edit_caption(
                caption=text(language, "premium_history_loading"),
                reply_markup=None,
            )
        elif isinstance(callback.message, Message):
            await callback.message.edit_text(text(language, "premium_history_loading"))
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            log.warning(
                "price_history_loading_state_failed",
                callback_data=callback.data,
                user_id=callback.from_user.id,
                game_id=rule.game_id,
                app_id=rule.game.steam_app_id,
                period=period_value,
                country=user.country_code,
                currency=latest.currency,
                message_type=message_type,
                message_id=getattr(callback.message, "message_id", None),
                error_type=type(error).__name__,
                exc_info=True,
            )
    try:
        full_history = await price_history.get_history(
            rule.game_id,
            rule.game.steam_app_id,
            user.country_code,
            latest.currency,
            None,
        )
        history = (
            full_history
            if period_days is None
            else await price_history.get_history(
                rule.game_id,
                rule.game.steam_app_id,
                user.country_code,
                latest.currency,
                period_days,
            )
        )
    except Exception:
        log.exception(
            "price_history_data_failed",
            callback_data=callback.data,
            user_id=callback.from_user.id,
            game_id=rule.game_id,
            app_id=rule.game.steam_app_id,
            period=period_value,
            country=user.country_code,
            currency=latest.currency,
            source="itad_or_fallback",
            points=0,
            message_type=message_type,
            message_id=getattr(callback.message, "message_id", None),
        )
        await _safe_edit_callback(
            callback,
            text(language, "premium_history_error"),
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=text(language, "btn_back"),
                            callback_data=f"premium_analytics:{rule.id}",
                        )
                    ]
                ]
            ),
        )
        return
    if not history.points:
        await _safe_edit_callback(
            callback,
            text(language, "premium_history_insufficient"),
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=text(language, "btn_back"),
                            callback_data=f"premium_analytics:{rule.id}",
                        )
                    ]
                ]
            ),
        )
        return
    analysis_target = rule.max_price
    if analysis_target is not None and rule.target_currency not in {None, latest.currency}:
        try:
            analysis_target, _ = await currency.convert(
                analysis_target,
                rule.target_currency,
                latest.currency,
            )
        except CurrencyError:
            analysis_target = None
    overall_analysis = price_analytics.analyze(
        full_history.points,
        latest.final_price,
        latest.initial_price,
        latest.discount_percent,
        analysis_target,
        rule.min_discount,
    )
    period_analysis = price_analytics.analyze(
        history.points,
        latest.final_price,
        latest.initial_price,
        latest.discount_percent,
        analysis_target,
        rule.min_discount,
    )
    analysis = replace(
        period_analysis,
        historical_low=overall_analysis.historical_low,
        historical_low_at=overall_analysis.historical_low_at,
        historical_low_discount=overall_analysis.historical_low_discount,
        low_difference=overall_analysis.low_difference,
        low_difference_percent=overall_analysis.low_difference_percent,
    )
    period_key = "all" if period_days is None else str(period_days)
    try:
        chart = await price_charts.render(
            points=history.points,
            currency=latest.currency,
            regular_price=latest.initial_price,
            historical_low=analysis.historical_low,
            target_price=analysis_target,
            labels={
                "title": text(language, "chart_title"),
                "price": text(language, "chart_price"),
                "regular": text(language, "chart_regular"),
                "low": text(language, "chart_low"),
                "target": text(language, "chart_target"),
                "axis_price": text(language, "chart_axis_price", currency=latest.currency),
                "locale": language,
            },
            cache_identity=f"{rule.game.steam_app_id}:{period_key}",
        )
    except Exception:
        log.exception(
            "price_history_chart_failed",
            callback_data=callback.data,
            user_id=callback.from_user.id,
            game_id=rule.game_id,
            app_id=rule.game.steam_app_id,
            period=period_key,
            country=user.country_code,
            currency=latest.currency,
            source=history.source,
            points=len(history.points),
            message_type=message_type,
            message_id=getattr(callback.message, "message_id", None),
        )
        await _safe_edit_callback(
            callback,
            text(language, "premium_history_error"),
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=text(language, "btn_back"),
                            callback_data=f"premium_analytics:{rule.id}",
                        )
                    ]
                ]
            ),
        )
        return
    if chart is None:
        await _safe_edit_callback(
            callback,
            text(language, "premium_history_insufficient"),
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=text(language, "btn_back"),
                            callback_data=f"premium_analytics:{rule.id}",
                        )
                    ]
                ]
            ),
        )
        return
    caption = price_analytics.build_history_summary(
        language,
        latest.currency,
        analysis,
        period_key,
        history.source,
    )
    media = InputMediaPhoto(
        media=BufferedInputFile(chart, filename=f"price-history-{rule.game.steam_app_id}.png"),
        caption=caption,
        parse_mode="HTML",
    )
    markup = price_history_keyboard(language, rule.id, period_key)
    try:
        if isinstance(callback.message, Message) and callback.message.photo:
            updated = await callback.message.edit_media(media=media, reply_markup=markup)
        else:
            if isinstance(callback.message, Message):
                try:
                    await callback.message.delete()
                except TelegramBadRequest:
                    pass
            updated = await callback.bot.send_photo(
                callback.from_user.id,
                media.media,
                caption=caption,
                parse_mode="HTML",
                reply_markup=markup,
            )
        await redis.set(
            f"price_history:message:{callback.from_user.id}:{rule.id}",
            str(updated.message_id),
            ex=86400,
        )
        log.info(
            "price_history_screen_rendered",
            callback_data=callback.data,
            user_id=callback.from_user.id,
            game_id=rule.game_id,
            app_id=rule.game.steam_app_id,
            period=period_key,
            country=user.country_code,
            currency=latest.currency,
            source=history.source,
            points=len(history.points),
            message_type="photo",
            message_id=updated.message_id,
        )
    except Exception:
        log.exception(
            "price_history_screen_failed",
            callback_data=callback.data,
            user_id=callback.from_user.id,
            game_id=rule.game_id,
            app_id=rule.game.steam_app_id,
            period=period_key,
            country=user.country_code,
            currency=latest.currency,
            source=history.source,
            points=len(history.points),
            message_type=message_type,
            message_id=getattr(callback.message, "message_id", None),
        )
        await callback.bot.send_message(callback.from_user.id, text(language, "ui_error"))


@router.callback_query(F.data.startswith("premium_compare:"))
async def premium_region_compare_start(
    callback: CallbackQuery, state: FSMContext, session_factory: async_sessionmaker
) -> None:
    identifier = None
    steam_app_id = None
    language = "ru"
    stage = "parse_callback"
    try:
        identifier_kind, identifier = _parse_compare_callback(callback.data or "")
        stage = "load_user_and_game"
        async with session_factory() as session:
            user = await _user(session, callback.from_user.id)
            language = user.language_code if user else "ru"
            if user is None:
                await callback.answer(text(language, "profile_missing"), show_alert=True)
                return
            query = select(WatchRule).options(selectinload(WatchRule.game))
            query = (
                query.where(WatchRule.id == identifier)
                if identifier_kind == "rule"
                else query.join(Game).where(Game.steam_app_id == identifier, WatchRule.user_id == user.id)
            )
            rule = await session.scalar(query)
            error_key = _comparison_rule_error(user, rule)
            if error_key:
                if error_key == "premium_required" and rule is not None:
                    await _show_premium_gate(callback, language, f"watch:{rule.id}")
                    return
                await callback.answer(text(language, error_key), show_alert=True)
                return
            steam_app_id = rule.game.steam_app_id
            if not REGIONS:
                await callback.answer(text(language, "compare_regions_missing"), show_alert=True)
                return
            comparison_currency = (
                user.comparison_currency if user.comparison_currency in COMPARISON_CURRENCIES else "USD"
            )
        stage = "initialize_state"
        await state.set_state(RegionCompare.selecting)
        await state.set_data(
            {
                "rule_id": rule.id,
                "steam_app_id": steam_app_id,
                "game_name": rule.game.name,
                "selected": [],
                "comparison_currency": comparison_currency,
            }
        )
        stage = "render_screen"
        await callback.message.edit_text(
            text(language, "compare_setup_screen", game=escape(rule.game.name), selected=0),
            reply_markup=comparison_groups_keyboard(language, 0, comparison_currency),
        )
        await callback.answer()
    except TelegramBadRequest as error:
        log.exception(
            "comparison_screen_telegram_error",
            telegram_id=callback.from_user.id,
            callback_data=callback.data,
            selected_id=identifier,
            steam_app_id=steam_app_id,
            language=language,
            stage=stage,
            exception_type=type(error).__name__,
        )
        await callback.answer(text(language, "compare_message_unavailable"), show_alert=True)
    except Exception as error:
        log.exception(
            "comparison_screen_failed",
            telegram_id=callback.from_user.id,
            callback_data=callback.data,
            selected_id=identifier,
            steam_app_id=steam_app_id,
            language=language,
            stage=stage,
            exception_type=type(error).__name__,
        )
        await callback.answer(text(language, "ui_error"), show_alert=True)


@router.callback_query(RegionCompare.selecting, F.data.startswith("compare_group:"))
async def compare_group(callback: CallbackQuery, state: FSMContext, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    group = callback.data.split(":", 1)[1]
    if group not in {region.geo_group for region in REGIONS.values()}:
        await callback.answer(text(language, "compare_regions_missing"), show_alert=True)
        return
    data = await state.get_data()
    await state.update_data(group=group)
    await callback.message.edit_text(
        text(language, "compare_group_title", group=text(language, f"region_group_{group}")),
        reply_markup=comparison_regions_keyboard(language, group, set(data.get("selected", []))),
    )
    await callback.answer()


@router.callback_query(RegionCompare.selecting, F.data.startswith("compare_toggle:"))
async def compare_toggle(callback: CallbackQuery, state: FSMContext, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    code = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected = set(data.get("selected", []))
    if code in selected:
        selected.remove(code)
    elif len(selected) >= MAX_COMPARISON_REGIONS:
        await callback.answer(text(language, "compare_limit", limit=MAX_COMPARISON_REGIONS), show_alert=True)
        return
    else:
        selected.add(code)
    await state.update_data(selected=sorted(selected))
    await callback.message.edit_reply_markup(
        reply_markup=comparison_regions_keyboard(language, data["group"], selected)
    )
    await callback.answer()


@router.callback_query(RegionCompare.selecting, F.data.startswith("compare_all:"))
async def compare_select_group(callback: CallbackQuery, state: FSMContext, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    group = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected = set(data.get("selected", []))
    group_codes = [code for code, region in REGIONS.items() if region.geo_group == group]
    if all(code in selected for code in group_codes):
        selected.difference_update(group_codes)
    else:
        available = MAX_COMPARISON_REGIONS - len(selected)
        selected.update([code for code in group_codes if code not in selected][:available])
    await state.update_data(selected=sorted(selected))
    await callback.message.edit_reply_markup(reply_markup=comparison_regions_keyboard(language, group, selected))
    await callback.answer()


@router.callback_query(RegionCompare.selecting, F.data == "compare:groups")
async def compare_groups(callback: CallbackQuery, state: FSMContext, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    data = await state.get_data()
    await callback.message.edit_text(
        text(
            language,
            "compare_setup_screen",
            game=escape(data.get("game_name") or "—"),
            selected=len(data.get("selected", [])),
        ),
        reply_markup=comparison_groups_keyboard(
            language, len(data.get("selected", [])), data.get("comparison_currency", "USD")
        ),
    )
    await callback.answer()


@router.callback_query(RegionCompare.selecting, F.data == "compare:clear")
async def compare_clear(callback: CallbackQuery, state: FSMContext, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    data = await state.get_data()
    await state.update_data(selected=[])
    await callback.message.edit_reply_markup(
        reply_markup=comparison_groups_keyboard(language, 0, data.get("comparison_currency", "USD"))
    )
    await callback.answer()


@router.callback_query(RegionCompare.selecting, F.data == "compare:currency")
async def compare_currency_menu(
    callback: CallbackQuery, state: FSMContext, session_factory: async_sessionmaker
) -> None:
    language = await _language(session_factory, callback.from_user.id)
    data = await state.get_data()
    await callback.message.edit_text(
        text(language, "compare_choose_currency"),
        reply_markup=comparison_currency_keyboard(language, data.get("comparison_currency", "USD")),
    )
    await callback.answer()


@router.callback_query(RegionCompare.selecting, F.data.startswith("compare_currency:"))
async def compare_currency_set(callback: CallbackQuery, state: FSMContext, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    currency_code = callback.data.split(":", 1)[1]
    if currency_code not in COMPARISON_CURRENCIES:
        await callback.answer(text(language, "invalid_value"), show_alert=True)
        return
    await state.update_data(comparison_currency=currency_code)
    data = await state.get_data()
    await callback.message.edit_text(
        text(
            language,
            "compare_setup_screen",
            game=escape(data.get("game_name") or "—"),
            selected=len(data.get("selected", [])),
        ),
        reply_markup=comparison_groups_keyboard(language, len(data.get("selected", [])), currency_code),
    )
    await callback.answer()


@router.callback_query(RegionCompare.selecting, F.data.in_({"compare:confirm", "compare:refresh"}))
async def premium_region_compare_result(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker,
    steam: SteamProvider,
    currency: CurrencyService,
) -> None:
    data = await state.get_data()
    force_refresh = callback.data == "compare:refresh"
    if not isinstance(data.get("rule_id"), int) or not isinstance(data.get("selected", []), list):
        language = await _language(session_factory, callback.from_user.id)
        await state.clear()
        await callback.answer(text(language, "compare_expired"), show_alert=True)
        return
    selected = [code for code in data.get("selected", []) if code in REGIONS]
    language = await _language(session_factory, callback.from_user.id)
    if len(selected) < 2:
        await callback.answer(text(language, "compare_minimum_two"), show_alert=True)
        return
    if force_refresh and not await steam.redis.set(f"compare:refresh:{callback.from_user.id}", "1", ex=30, nx=True):
        await callback.answer(text(language, "compare_refresh_limited"), show_alert=True)
        return
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        rule = await session.scalar(
            select(WatchRule)
            .options(selectinload(WatchRule.game))
            .where(WatchRule.id == data["rule_id"], WatchRule.user_id == getattr(user, "id", 0))
        )
        if not user or not user.is_premium or not rule:
            await callback.answer(text(language, "premium_required"), show_alert=True)
            return
        user.comparison_regions = selected
        user.comparison_currency = data.get("comparison_currency", "USD")
        await session.commit()
        cutoff = datetime.now(UTC) - timedelta(hours=1)
        snapshots = (
            await session.scalars(
                select(PriceSnapshot)
                .where(
                    PriceSnapshot.game_id == rule.game_id,
                    PriceSnapshot.country_code.in_(selected),
                    PriceSnapshot.checked_at >= cutoff,
                )
                .order_by(PriceSnapshot.country_code, PriceSnapshot.checked_at.desc())
            )
        ).all()
    if callback.message:
        await callback.message.edit_text(text(language, "compare_loading", currency=user.comparison_currency))
    await callback.answer()
    cached: dict[str, CachedRegionalPrice] = {}
    for snapshot in snapshots:
        if force_refresh:
            break
        if snapshot.country_code not in cached:
            cached[snapshot.country_code] = CachedRegionalPrice(
                SteamPrice(
                    app_id=rule.game.steam_app_id,
                    currency=snapshot.currency,
                    initial=snapshot.initial_price,
                    final=snapshot.final_price,
                    discount_percent=snapshot.discount_percent,
                ),
                snapshot.checked_at,
            )
    comparison_service = RegionalPriceComparison(steam, concurrency=5, timeout=6)
    fetched = await comparison_service.fetch(
        rule.game.steam_app_id,
        selected,
        cached,
        _steam_language(language),
        force=force_refresh,
    )
    if not fetched.prices:
        await callback.message.edit_text(
            text(language, "compare_unavailable"), reply_markup=back_keyboard("premium", language)
        )
        return
    currencies = {item.price.currency for item in fetched.prices}
    try:
        rates_info = (
            await currency.rates()
            if currencies != {user.comparison_currency}
            else ExchangeRates({user.comparison_currency: Decimal(1)}, datetime.now(UTC), source="identity")
        )
    except CurrencyError:
        log.warning("comparison_rates_failed", steam_app_id=rule.game.steam_app_id, exc_info=True)
        rates_info = ExchangeRates(
            {user.comparison_currency: Decimal(1)}, datetime.now(UTC), stale=True, source="unavailable"
        )
    comparison = comparison_service.apply_rates(fetched, user.comparison_currency, rates_info, rule.game.steam_app_id)
    converted = [item for item in comparison.prices if item.converted is not None]
    if not comparison.prices:
        await callback.message.edit_text(
            text(language, "compare_unavailable"), reply_markup=back_keyboard("premium", language)
        )
        return
    converted.sort(key=lambda item: item.converted)
    cheapest = converted[0].converted if converted else None
    lines = []
    for index, item in enumerate(converted):
        code, price, value = item.region, item.price, item.converted
        difference = value - cheapest
        percent = Decimal(0) if cheapest == 0 else (difference / cheapest * 100).quantize(Decimal("1"))
        status = (
            text(language, "compare_cheapest")
            if index == 0
            else text(
                language,
                "compare_more_expensive",
                difference=format_money(difference, user.comparison_currency),
                percent=percent,
            )
        )
        lines.append(
            text(
                language,
                "compare_result_item",
                region=text(language, REGIONS[code].translation_key),
                original=format_money(price.final, price.currency),
                converted=format_money(value, user.comparison_currency),
                status=status,
            )
        )
    for item in comparison.prices:
        if item.converted is None:
            lines.append(
                text(
                    language,
                    "compare_original_only",
                    region=text(language, REGIONS[item.region].translation_key),
                    original=format_money(item.price.final, item.price.currency),
                )
            )
    for code in comparison.unavailable:
        lines.append(
            text(
                language,
                "compare_unavailable_item",
                region=text(language, REGIONS[code].translation_key),
            )
        )
    warnings = []
    if comparison.unavailable:
        missing = ", ".join(text(language, REGIONS[c].translation_key) for c in comparison.unavailable)
        warnings.append(text(language, "compare_prices_missing", regions=missing))
    if comparison.unconverted:
        missing = ", ".join(text(language, REGIONS[c].translation_key) for c in comparison.unconverted)
        warnings.append(text(language, "compare_conversion_missing", regions=missing))
    updated = rates_info.updated_at.astimezone(timezone_from_name(user.timezone)).strftime("%d.%m.%Y %H:%M %Z")
    content = text(
        language,
        "compare_result",
        game=escape(rule.game.name),
        items="\n\n".join(lines),
        currency=user.comparison_currency,
        updated=updated,
        stale=(text(language, "compare_rates_stale") if rates_info.stale else "")
        + ("\n" + "\n".join(warnings) if warnings else ""),
    )
    await callback.message.edit_text(
        content,
        reply_markup=comparison_result_keyboard(language),
    )


@router.callback_query(F.data.startswith("compare"))
async def expired_compare_callback(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    await callback.answer(text(language, "compare_expired"), show_alert=True)


@router.callback_query(F.data == "menu:giveaways")
async def giveaways(
    callback: CallbackQuery, session_factory: async_sessionmaker, redis: Redis, settings: Settings
) -> None:
    async with session_factory() as session:
        items = (
            await session.scalars(
                select(Giveaway).where(Giveaway.approved.is_(True), Giveaway.active.is_(True)).limit(20)
            )
        ).all()
        user = await _user(session, callback.from_user.id)
    status = await redis.get("sync:full:status")
    last_updated = await redis.get("sync:giveaways:last_success")
    if not items and status in {None, "running"}:
        await callback.message.edit_text(
            text(user.language_code, "giveaways_loading"),
            reply_markup=giveaways_keyboard(user, user.language_code),
        )
    elif not items:
        await callback.message.edit_text(
            text(user.language_code, "giveaways_empty")
            + _updated_label(last_updated, user.timezone, user.language_code),
            reply_markup=giveaways_keyboard(user, user.language_code),
        )
    else:
        await callback.message.edit_text(
            text(user.language_code, "giveaways_title")
            + "\n\n"
            + "\n".join(
                f'• <a href="{item.url}">{item.title}</a> · {_giveaway_kind(item, user.language_code)}'
                for item in items
            )
            + _updated_label(last_updated, user.timezone, user.language_code)
            + "\n\n"
            + text(user.language_code, "source", source='<a href="https://www.gamerpower.com/">GamerPower</a>'),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=giveaways_keyboard(user, user.language_code),
        )
    await callback.answer()


@router.callback_query(F.data == "giveaway:toggle")
async def giveaway_notifications_toggle(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        user.giveaway_notifications_enabled = not user.giveaway_notifications_enabled
        enabled = user.giveaway_notifications_enabled
        language = user.language_code
        await session.commit()
    await callback.message.edit_reply_markup(reply_markup=giveaways_keyboard(user, language))
    await callback.answer(
        text(language, "giveaway_notifications_changed", state=text(language, "enabled" if enabled else "disabled"))
    )


@router.callback_query(F.data == "giveaway:settings")
async def giveaway_type_settings(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    language = user.language_code
    if not user.is_premium:
        await _show_premium_gate(callback, language, "menu:giveaways", "giveaways")
        return
    await callback.message.edit_text(
        text(language, "giveaway_types_screen"),
        reply_markup=giveaway_types_keyboard(user, language),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("giveaway_kind:"))
async def giveaway_kind_toggle(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    kind = callback.data.split(":", 1)[1]
    if kind not in {"keep", "weekend", "dlc"}:
        await callback.answer()
        return
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        language = user.language_code
        if not user.is_premium:
            await _show_premium_gate(callback, language, "menu:giveaways")
            return
        selected = set(user.giveaway_notification_kinds or [])
        selected.symmetric_difference_update({kind})
        user.giveaway_notification_kinds = sorted(selected)
        await session.commit()
    await callback.message.edit_reply_markup(reply_markup=giveaway_types_keyboard(user, language))
    await callback.answer(text(language, "giveaway_types_saved"))


@router.callback_query(F.data.startswith("giveaway_kinds:"))
async def giveaway_kinds_bulk(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    mode = callback.data.split(":", 1)[1]
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        language = user.language_code
        if not user.is_premium:
            await _show_premium_gate(callback, language, "menu:giveaways")
            return
        user.giveaway_notification_kinds = ["keep", "weekend", "dlc"] if mode == "all" else []
        await session.commit()
    await callback.message.edit_reply_markup(reply_markup=giveaway_types_keyboard(user, language))
    await callback.answer(text(language, "giveaway_types_saved"))


def _deal_value_score(
    item,
    low: ExternalHistoricalLow | None,
    history: list | None = None,
    now: datetime | None = None,
) -> int | None:
    now = now or datetime.now(UTC)
    source = history or [item]
    snapshots = [
        AnalyticsSnapshot(
            row.final_price,
            row.initial_price,
            row.currency,
            getattr(row, "checked_at", now),
        )
        for row in source
        if row.final_price is not None
    ]
    return calculate_purchase_score(
        snapshots,
        low.price if low else None,
        low.currency if low else None,
        now,
    )


def _deal_rating_key(score: int) -> str:
    if score >= 90:
        return "deals_rating_excellent"
    if score >= 75:
        return "deals_rating_very_good"
    if score >= 55:
        return "deals_rating_normal"
    if score >= 35:
        return "deals_rating_wait"
    return "deals_rating_bad"


def _sort_deal_entries(
    entries: list,
    sort_key: str,
    low_by_game: dict[int, ExternalHistoricalLow],
    histories: dict[int, list] | None = None,
) -> list:
    histories = histories or {}

    def price_or_infinity(item) -> Decimal:
        return item.final_price if item.final_price is not None else Decimal("Infinity")

    if sort_key == "price":
        return sorted(entries, key=lambda item: (item.final_price is None, price_or_infinity(item)))
    if sort_key == "value":
        return sorted(
            entries,
            key=lambda item: (
                _deal_value_score(item, low_by_game.get(item.game_id), histories.get(item.game_id)) is None,
                -(_deal_value_score(item, low_by_game.get(item.game_id), histories.get(item.game_id)) or 0),
                price_or_infinity(item),
            ),
        )
    if sort_key == "name":
        return sorted(entries, key=lambda item: item.name.casefold())
    return sorted(entries, key=lambda item: (-(item.discount_percent or 0), price_or_infinity(item)))


def _default_deals_state() -> dict:
    return {
        "current_page": 0,
        "sort_mode": "discount",
        "min_discount": 0,
        "max_price": 0,
        "historical_low_only": False,
        "free_only": False,
        "tracked_only": True,
        "selected_currency": None,
        "cached_offer_ids": [],
        "fetched_at": None,
    }


def _parse_deal_discount(value: str) -> Decimal:
    parsed = Decimal(value.strip().replace(",", "."))
    if not parsed.is_finite() or parsed < 0 or parsed > 100:
        raise ValueError("discount out of range")
    return parsed.quantize(Decimal("0.01"))


def _parse_deal_price(value: str) -> Decimal:
    parsed = Decimal(value.strip().replace(",", "."))
    if not parsed.is_finite() or parsed <= 0 or parsed > Decimal("1000000"):
        raise ValueError("price out of range")
    return parsed.quantize(Decimal("0.01"))


def _decimal_filter_string(value: Decimal) -> str:
    return format(value, "f").rstrip("0").rstrip(".") or "0"


def _deals_filter_screen_text(language: str, deal_state: dict, currency: str) -> str:
    discount = Decimal(str(deal_state.get("min_discount") or 0))
    price = Decimal(str(deal_state.get("max_price") or 0))
    return text(
        language,
        "deals_filter_screen_values",
        discount=f"{_decimal_filter_string(discount)}%" if discount > 0 else text(language, "deals_filter_not_set"),
        price=format_money(price, currency) if price > 0 else text(language, "deals_filter_not_set"),
    )


@router.callback_query(F.data == "menu:deals")
@router.callback_query(F.data == "deals:return")
@router.callback_query(F.data.in_({"deals:sort_screen", "deals:filter_screen", "deals:analytics_screen"}))
@router.callback_query(F.data.startswith("deals_page:"))
@router.callback_query(F.data.startswith("deals_sort:"))
@router.callback_query(F.data.startswith("deals_filter:"))
@router.callback_query(F.data.startswith("deals_filter_clear:"))
async def deals(
    callback: CallbackQuery,
    session_factory: async_sessionmaker,
    redis: Redis,
    state: FSMContext,
) -> None:
    state_key = f"deals:state:{callback.from_user.id}"
    raw_state = await redis.get(state_key)
    try:
        deal_state = {**_default_deals_state(), **(json.loads(raw_state) if raw_state else {})}
    except (TypeError, ValueError):
        deal_state = _default_deals_state()
    deal_state["min_discount"] = str(deal_state.get("min_discount") or "0")
    deal_state["max_price"] = str(deal_state.get("max_price") or "0")
    if callback.data == "deals:filter_screen":
        await state.clear()
    if callback.data == "menu:deals":
        deal_state["current_page"] = 0
    if callback.data.startswith("deals_page:"):
        deal_state["current_page"] = int(callback.data.split(":", 1)[1])
    sort_key = deal_state["sort_mode"]
    if callback.data.startswith("deals_sort:"):
        sort_key = callback.data.split(":", 1)[1]
        if sort_key not in {"discount", "price", "value", "name"}:
            sort_key = "discount"
        deal_state["sort_mode"] = sort_key
        deal_state["current_page"] = 0
    if callback.data.startswith("deals_filter:"):
        kind = callback.data.split(":", 1)[1]
        if kind in {"discount", "max_price"}:
            async with session_factory() as session:
                input_user = await _user(session, callback.from_user.id)
            language = input_user.language_code if input_user else "ru"
            currency_code = deal_state.get("selected_currency") or (
                REGIONS[input_user.country_code].currency if input_user else "USD"
            )
            current = deal_state["min_discount" if kind == "discount" else "max_price"]
            await state.set_state(DealsFilterSetup.value)
            await state.set_data(
                {
                    "filter_kind": kind,
                    "panel_chat_id": callback.message.chat.id,
                    "panel_message_id": callback.message.message_id,
                    "currency": currency_code,
                }
            )
            key = "deals_filter_discount_prompt" if kind == "discount" else "deals_filter_price_prompt"
            current_label = (
                text(language, "deals_filter_not_set")
                if Decimal(current) == 0
                else (f"{current}%" if kind == "discount" else format_money(Decimal(current), currency_code))
            )
            await callback.message.edit_text(
                text(language, key, current=current_label, currency=currency_code),
                reply_markup=deals_filter_input_keyboard(language, kind),
            )
            await callback.answer()
            return
        if kind == "historical":
            deal_state["historical_low_only"] = not deal_state["historical_low_only"]
        elif kind == "free":
            deal_state["free_only"] = not deal_state["free_only"]
        elif kind == "tracked":
            language = await _language(session_factory, callback.from_user.id)
            await callback.answer(text(language, "deals_tracked_fixed"), show_alert=True)
            return
        elif kind == "reset":
            preserved_sort = deal_state["sort_mode"]
            deal_state = _default_deals_state()
            deal_state["sort_mode"] = preserved_sort
        deal_state["current_page"] = 0
    if callback.data.startswith("deals_filter_clear:"):
        kind = callback.data.split(":", 1)[1]
        deal_state["min_discount" if kind == "discount" else "max_price"] = "0"
        deal_state["current_page"] = 0
        await state.clear()
    await redis.set(state_key, json.dumps(deal_state), ex=3600)
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        rows = (
            await session.execute(
                select(
                    WatchRule.id.label("rule_id"),
                    Game.id.label("game_id"),
                    Game.name,
                    Game.steam_app_id,
                    PriceSnapshot.initial_price,
                    PriceSnapshot.final_price,
                    PriceSnapshot.currency,
                    PriceSnapshot.discount_percent,
                    PriceSnapshot.checked_at,
                )
                .join(WatchRule, WatchRule.game_id == Game.id)
                .join(PriceSnapshot, PriceSnapshot.game_id == Game.id)
                .where(
                    WatchRule.user_id == user.id,
                    PriceSnapshot.country_code == user.country_code,
                    PriceSnapshot.discount_percent > 0,
                )
                .order_by(PriceSnapshot.checked_at.desc())
                .limit(100)
            )
        ).all()
        lows = list(
            (
                await session.scalars(
                    select(ExternalHistoricalLow).where(
                        ExternalHistoricalLow.country_code == user.country_code,
                        ExternalHistoricalLow.scope == "steam",
                    )
                )
            ).all()
        )
    if not rows:
        await callback.message.edit_text(
            text(user.language_code, "deals_empty"),
            reply_markup=back_keyboard("home", user.language_code),
        )
    else:
        low_by_game = {item.game_id: item for item in lows}
        histories: dict[int, list] = {}
        for row in reversed(rows):
            histories.setdefault(row.game_id, []).append(row)
        seen: set[int] = set()
        entries = []
        for row in rows:
            if row.steam_app_id in seen or row.final_price is None or row.final_price < 0:
                continue
            seen.add(row.steam_app_id)
            entries.append(row)
        min_discount = Decimal(deal_state["min_discount"])
        max_price = Decimal(deal_state["max_price"])
        entries = [item for item in entries if Decimal(item.discount_percent) >= min_discount]
        if max_price > 0:
            entries = [item for item in entries if item.final_price <= max_price]
        if deal_state["free_only"]:
            entries = [item for item in entries if item.final_price == 0]
        if deal_state["historical_low_only"]:
            entries = [
                item
                for item in entries
                if (low := low_by_game.get(item.game_id))
                and low.currency == item.currency
                and item.final_price <= low.price
            ]
        entries = _sort_deal_entries(entries, sort_key, low_by_game, histories)
        page_size = 5
        pages = max(1, (len(entries) + page_size - 1) // page_size)
        page = max(0, min(int(deal_state["current_page"]), pages - 1))
        deal_state["current_page"] = page
        page_entries = entries[page * page_size : (page + 1) * page_size]
        deal_state["cached_offer_ids"] = [row.rule_id for row in entries]
        deal_state["selected_currency"] = page_entries[0].currency if page_entries else None
        deal_state["fetched_at"] = datetime.now(UTC).isoformat()
        await redis.set(state_key, json.dumps(deal_state), ex=3600)
        if callback.data == "deals:sort_screen":
            await callback.message.edit_text(
                text(user.language_code, "deals_sort_screen"),
                reply_markup=deals_sort_keyboard(user.language_code, sort_key),
            )
            await callback.answer()
            return
        if (
            callback.data == "deals:filter_screen"
            or callback.data.startswith("deals_filter:")
            or callback.data.startswith("deals_filter_clear:")
        ):
            await callback.message.edit_text(
                _deals_filter_screen_text(
                    user.language_code,
                    deal_state,
                    deal_state.get("selected_currency") or REGIONS[user.country_code].currency,
                ),
                reply_markup=deals_filters_keyboard(user.language_code, deal_state),
            )
            await callback.answer()
            return
        if callback.data == "deals:analytics_screen":
            if not user.is_premium:
                await _show_premium_gate(callback, user.language_code, "deals:return")
                return
            await callback.message.edit_text(
                text(user.language_code, "deals_choose_analytics"),
                reply_markup=deals_analytics_keyboard(
                    user.language_code, [(row.rule_id, row.name) for row in page_entries]
                ),
            )
            await callback.answer()
            return
        lines = [text(user.language_code, "deals_title")]
        for index, row in enumerate(page_entries, start=1 + page * page_size):
            low = low_by_game.get(row.game_id)
            historical = ""
            if user.is_premium and low and low.currency == row.currency and low.price > 0:
                difference = row.final_price - low.price
                historical = (
                    "\n" + text(user.language_code, "deals_at_low")
                    if difference <= 0
                    else "\n"
                    + text(
                        user.language_code,
                        "deals_historical",
                        value=format_money(low.price, row.currency),
                        difference=text(
                            user.language_code,
                            "deals_above_low",
                            value=format_money(difference, row.currency),
                        ),
                    )
                )
            rating = ""
            if user.is_premium:
                score = _deal_value_score(row, low, histories.get(row.game_id))
                if score is not None:
                    rating = "\n" + text(
                        user.language_code,
                        "deals_rating",
                        score=score,
                        verdict=text(user.language_code, _deal_rating_key(score)),
                    )
            lines.append(
                text(
                    user.language_code,
                    "deals_card",
                    index=index,
                    url=f"https://store.steampowered.com/app/{row.steam_app_id}",
                    name=escape(row.name),
                    discount=row.discount_percent,
                    current=format_money(row.final_price, row.currency),
                    regular=(
                        format_money(row.initial_price, row.currency)
                        if row.initial_price and row.initial_price > row.final_price
                        else text(user.language_code, "no_data")
                    ),
                    rating=rating,
                    historical=historical,
                )
            )
        if page_entries:
            lines.append("━━━━━━━━━━━━━━━━━━")
        filter_parts = []
        if min_discount > 0:
            filter_parts.append(
                text(user.language_code, "deals_filter_summary_discount", value=deal_state["min_discount"])
            )
        if max_price > 0:
            currency = page_entries[0].currency if page_entries else ""
            filter_parts.append(
                text(
                    user.language_code,
                    "deals_filter_summary_price",
                    value=format_money(max_price, currency),
                )
            )
        if deal_state["historical_low_only"]:
            filter_parts.append(text(user.language_code, "deals_filter_historical"))
        if deal_state["free_only"]:
            filter_parts.append(text(user.language_code, "deals_filter_free"))
        lines.append(
            text(user.language_code, "deals_sort_summary", value=text(user.language_code, f"deals_sort_{sort_key}"))
        )
        lines.append(
            text(
                user.language_code,
                "deals_filters_summary",
                value=", ".join(filter_parts) if filter_parts else text(user.language_code, "deals_filters_none"),
            )
        )
        lines.append(text(user.language_code, "pagination", page=page + 1, pages=pages))
        await callback.message.edit_text(
            "\n\n".join(lines),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=deals_keyboard(
                user.language_code,
                page,
                pages,
                sort_key,
                [(row.rule_id, row.name) for row in page_entries],
            ),
        )
    await callback.answer()


@router.message(DealsFilterSetup.value)
async def deals_filter_value_save(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker,
    redis: Redis,
) -> None:
    data = await state.get_data()
    kind = data.get("filter_kind")
    language = await _language(session_factory, message.from_user.id)
    try:
        value = (
            _parse_deal_discount(message.text or "") if kind == "discount" else _parse_deal_price(message.text or "")
        )
    except (InvalidOperation, ValueError):
        await _replace_panel(
            message,
            state,
            text(language, "deals_filter_invalid_discount" if kind == "discount" else "deals_filter_invalid_price"),
            deals_filter_input_keyboard(language, kind or "discount"),
        )
        return
    state_key = f"deals:state:{message.from_user.id}"
    raw = await redis.get(state_key)
    try:
        deal_state = {**_default_deals_state(), **(json.loads(raw) if raw else {})}
    except (TypeError, ValueError):
        deal_state = _default_deals_state()
    deal_state["min_discount" if kind == "discount" else "max_price"] = _decimal_filter_string(value)
    deal_state["current_page"] = 0
    currency_code = data.get("currency") or "USD"
    await redis.set(state_key, json.dumps(deal_state), ex=3600)
    await _replace_panel(
        message,
        state,
        _deals_filter_screen_text(language, deal_state, currency_code),
        deals_filters_keyboard(language, deal_state),
    )
    await state.clear()


@router.callback_query()
async def expired_callback(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    """Always stop Telegram's spinner for stale buttons left after a deployment."""
    language = await _language(session_factory, callback.from_user.id)
    await callback.answer(text(language, "callback_expired"), show_alert=True)


@router.error()
async def error_handler(event, session_factory: async_sessionmaker) -> bool:
    callback = getattr(event.update, "callback_query", None)
    message = getattr(event.update, "message", None)
    telegram_user = getattr(callback or message, "from_user", None)
    language = await _language(session_factory, telegram_user.id) if telegram_user is not None else "ru"
    log.error(
        "telegram_update_failed",
        error=str(event.exception),
        exception_type=type(event.exception).__name__,
        user_id=getattr(telegram_user, "id", None),
        language=language,
        callback_data=getattr(callback, "data", None),
        message_id=getattr(getattr(callback, "message", None) or message, "message_id", None),
        exc_info=(type(event.exception), event.exception, event.exception.__traceback__),
    )
    if callback is not None:
        try:
            await callback.answer(text(language, "ui_error"), show_alert=True)
        except TelegramBadRequest:
            pass
    return True


def _profile_screen(user: User, timezone_name: str) -> tuple[str, InlineKeyboardMarkup]:
    country = user.country_code if user.country_code in REGIONS else "KZ"
    language = user.language_code if user.language_code in LANGUAGES else "ru"
    created_at = getattr(user, "created_at", None) or datetime.now(UTC)
    user_timezone = getattr(user, "timezone", None) or timezone_name
    try:
        zone = timezone_from_name(user_timezone, timezone_name)
        registered = created_at.astimezone(zone).strftime("%d.%m.%Y")
    except (ZoneInfoNotFoundError, AttributeError):
        registered = created_at.strftime("%d.%m.%Y")
    username = getattr(user, "username", None)
    nickname = f"@{username}" if username else str(getattr(user, "telegram_id", "—"))
    content = text(
        language,
        "profile",
        nickname=nickname,
        language=LANGUAGES[language],
        region=text(language, f"region_{country.lower()}"),
        currency=REGIONS[country].currency,
        timezone=user_timezone,
        plan="Premium" if user.is_premium else "Free",
        registered=registered,
    )
    if user.is_premium and user.premium_until:
        content += "\n\n" + text(
            language, "premium_active", date=user.premium_until.astimezone(zone).strftime("%d.%m.%Y")
        )
    return content, profile_keyboard(language)


def _premium_screen(
    user: User | None,
    language: str,
    test_mode: bool,
    tracked: int = 0,
    *,
    is_admin: bool = False,
) -> tuple[str, InlineKeyboardMarkup]:
    if user and user.is_premium and user.premium_until:
        now = datetime.now(UTC)
        remaining_label = _premium_remaining(language, user.premium_until - now)
        try:
            zone = timezone_from_name(getattr(user, "timezone", "UTC"))
        except (ZoneInfoNotFoundError, TypeError):
            zone = ZoneInfo("UTC")
        started = (user.premium_started_at or user.created_at).astimezone(zone).strftime("%d.%m.%Y %H:%M")
        content = text(
            language,
            "premium_screen_active",
            started=started,
            until=user.premium_until.astimezone(zone).strftime("%d.%m.%Y %H:%M %Z"),
            remaining=remaining_label,
            tracked=tracked,
        )
        return content, active_premium_keyboard(language, is_admin=is_admin)
    content = text(language, "premium")
    if test_mode:
        content += "\n\n" + text(language, "premium_test_label")
    return content, premium_keyboard(language, is_admin=is_admin)


def _premium_remaining(language: str, remaining: timedelta) -> str:
    total_minutes = max(0, int(remaining.total_seconds() // 60))
    days, rest = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rest, 60)
    parts = []
    if days:
        parts.append(text(language, "remaining_days", count=days))
    if hours:
        parts.append(text(language, "remaining_hours", count=hours))
    if not days and minutes:
        parts.append(text(language, "remaining_minutes", count=minutes))
    return " ".join(parts) or text(language, "remaining_less_minute")


def _digest_screen(user: User) -> str:
    language = user.language_code
    daily_time = (user.daily_digest_time or time(20)).strftime("%H:%M")
    weekly_time = (user.weekly_digest_time or time(18)).strftime("%H:%M")
    return text(
        language,
        "digest_screen",
        daily_status=text(language, "enabled_title" if user.daily_digest_enabled else "disabled_title"),
        daily_time=daily_time,
        weekly_status=text(language, "enabled_title" if user.weekly_digest_enabled else "disabled_title"),
        weekday=text(language, f"weekday_{user.weekly_digest_weekday}"),
        weekly_time=weekly_time,
        timezone=user.timezone,
    )


def _digest_kind_screen(user: User, kind: str) -> str:
    language = user.language_code
    enabled = user.daily_digest_enabled if kind == "daily" else user.weekly_digest_enabled
    send_time = user.daily_digest_time if kind == "daily" else user.weekly_digest_time
    return text(
        language,
        f"digest_{kind}_screen",
        status=text(language, "enabled_title" if enabled else "disabled_title"),
        time=(send_time or time(20 if kind == "daily" else 18)).strftime("%H:%M"),
        weekday=text(language, f"weekday_{user.weekly_digest_weekday}"),
    )


def _settings_screen(user: User) -> tuple[str, InlineKeyboardMarkup]:
    """Backward-compatible name used by integrations; settings is now the profile screen."""
    return _profile_screen(user, "UTC")


async def _safe_edit_callback(callback: CallbackQuery, content: str, markup: InlineKeyboardMarkup) -> None:
    message = callback.message
    if not isinstance(message, Message):
        await callback.bot.send_message(callback.from_user.id, content, reply_markup=markup)
        return
    try:
        await message.edit_text(content, reply_markup=markup)
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            try:
                await message.edit_reply_markup(reply_markup=markup)
            except TelegramBadRequest as markup_error:
                if "message is not modified" not in str(markup_error).lower():
                    raise
            return
        log.warning(
            "settings_edit_failed",
            error=str(error),
            chat_id=message.chat.id,
            message_id=message.message_id,
        )
        if message.photo or message.document:
            try:
                await message.delete()
            except TelegramBadRequest:
                pass
        await callback.bot.send_message(message.chat.id, content, reply_markup=markup)


def _steam_language(language: str | None) -> str:
    return {"ru": "russian", "en": "english", "kk": "russian"}.get(language or "ru", "russian")


def _steam_country(region_id: str | None) -> str:
    return (REGIONS.get(region_id or "") or REGIONS["KZ"]).steam_country_code


def _normalized(price) -> NormalizedPrice | None:
    if price is None:
        return None
    return NormalizedPrice(
        currency=price.currency, initial=price.initial, final=price.final, discount_percent=price.discount_percent
    )


def _watch_card_content(rule: WatchRule, latest: PriceSnapshot | None, user: User) -> str:
    condition = (
        text(user.language_code, "condition_discount", value=rule.min_discount)
        if rule.min_discount is not None
        else text(
            user.language_code,
            "condition_price",
            value=format_money(rule.max_price, rule.target_currency or REGIONS[user.country_code].currency),
        )
    )
    normalized = (
        NormalizedPrice(
            currency=latest.currency,
            initial=latest.initial_price,
            final=latest.final_price,
            discount_percent=latest.discount_percent,
        )
        if latest
        else None
    )
    notifications = text(user.language_code, "enabled" if rule.notifications_enabled else "disabled")
    updated = ""
    if latest is not None:
        try:
            local_checked = latest.checked_at.astimezone(timezone_from_name(user.timezone))
            updated = "\n" + text(
                user.language_code, "last_updated", date=f"{local_checked:%d.%m.%Y %H:%M} {user.timezone}"
            )
        except (ZoneInfoNotFoundError, AttributeError):
            updated = ""
    return (
        format_price_card(rule.game.name, normalized, user.language_code)
        + updated
        + f"\n\n{text(user.language_code, 'condition')}: <b>{condition}</b>"
        + f"\n{text(user.language_code, 'notifications')}: <b>{notifications}</b>"
    )


async def _owns_rule(session_factory: async_sessionmaker, telegram_id: int, rule_id: int) -> bool:
    async with session_factory() as session:
        owned = await session.scalar(
            select(WatchRule.id)
            .join(User, User.id == WatchRule.user_id)
            .where(WatchRule.id == rule_id, User.telegram_id == telegram_id)
        )
    return owned is not None


def _giveaway_kind(item: Giveaway, language: str) -> str:
    return {
        "keep": text(language, "giveaway_keep"),
        "weekend": text(language, "giveaway_weekend"),
        "dlc": text(language, "giveaway_dlc"),
        "free_to_play": text(language, "giveaway_f2p"),
    }.get(item.kind.value, item.store)


def _updated_label(value: str | None, timezone_name: str, language: str = "ru") -> str:
    if not value:
        return "\n\n" + text(language, "never_updated")
    try:
        zone = timezone_from_name(timezone_name)
        localized = datetime.fromisoformat(value).astimezone(zone)
        offset = localized.strftime("%z")
        offset_label = f"UTC{offset[:3]}:{offset[3:]}" if offset else "UTC"
        stamp = f"{localized:%d.%m.%Y %H:%M} {localized.tzname()} ({offset_label})"
    except (ValueError, ZoneInfoNotFoundError):
        stamp = value
    return "\n\n" + text(language, "last_updated", date=stamp)


async def _language(session_factory: async_sessionmaker, telegram_id: int) -> str:
    async with session_factory() as session:
        user = await _user(session, telegram_id)
    return user.language_code if user and user.language_code in LANGUAGES else "ru"


async def _refresh_user_prices(
    session_factory: async_sessionmaker, steam: SteamProvider, user_id: int, country: str, language: str
) -> tuple[int, int]:
    async with session_factory() as session:
        rules = list(
            (
                await session.scalars(
                    select(WatchRule).options(selectinload(WatchRule.game)).where(WatchRule.user_id == user_id)
                )
            ).all()
        )
        updated = failed = 0
        for rule in rules:
            try:
                remote_game, price = await steam.details(
                    rule.game.steam_app_id, _steam_country(country), _steam_language(language), force_refresh=True
                )
                if price is None:
                    failed += 1
                    continue
                rule.game.name, rule.game.header_image = remote_game.name, remote_game.header_image
                session.add(
                    PriceSnapshot(
                        game_id=rule.game_id,
                        country_code=country,
                        currency=price.currency,
                        initial_price=price.initial,
                        final_price=price.final,
                        discount_percent=price.discount_percent,
                        checked_at=datetime.now(UTC),
                    )
                )
                updated += 1
            except SteamError:
                failed += 1
        await session.commit()
    return updated, failed


@dataclass(frozen=True)
class PremiumPurchase:
    months: int
    days: int
    stars: int


def _parse_premium_payload(
    payload: str,
    amount: int,
    *,
    allow_admin_test: bool = False,
) -> PremiumPurchase | None:
    try:
        prefix, tariff_code = payload.split(":", 1)
    except (ValueError, AttributeError):
        return None
    if prefix != "premium":
        return None
    if tariff_code == ADMIN_TEST_PREMIUM_CODE:
        if not allow_admin_test or amount != ADMIN_TEST_PREMIUM_STARS:
            return None
        return PremiumPurchase(months=0, days=ADMIN_TEST_PREMIUM_DAYS, stars=amount)
    try:
        months = int(tariff_code)
    except ValueError:
        return None
    if PREMIUM_PRICES.get(months) != amount:
        return None
    return PremiumPurchase(months=months, days=30 * months, stars=amount)


async def _replace_panel(message: Message, state: FSMContext, content: str, reply_markup) -> None:
    """Remove user input and replace the current bot panel without growing the chat."""
    data = await state.get_data()
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    chat_id = data.get("panel_chat_id", message.chat.id)
    message_id = data.get("panel_message_id")
    if message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=content,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
            return
        except TelegramBadRequest:
            pass
    panel = await message.answer(content, reply_markup=reply_markup, parse_mode="HTML")
    await state.update_data(panel_chat_id=panel.chat.id, panel_message_id=panel.message_id)
