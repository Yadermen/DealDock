from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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
    back_keyboard,
    comparison_currency_keyboard,
    comparison_groups_keyboard,
    comparison_regions_keyboard,
    digest_keyboard,
    digest_kind_keyboard,
    games_keyboard,
    hour_keyboard,
    info_keyboard,
    info_page_keyboard,
    invoice_keyboard,
    language_keyboard,
    main_keyboard,
    minute_keyboard,
    premium_filters_keyboard,
    premium_games_keyboard,
    premium_keyboard,
    profile_keyboard,
    quiet_hours_keyboard,
    region_groups_keyboard,
    region_keyboard,
    rule_keyboard,
    timezone_groups_keyboard,
    timezone_keyboard,
    watch_card_keyboard,
    watch_list_keyboard,
    weekday_keyboard,
)
from steam_radar.bot.states import AddGame, EditWatch, Onboarding, PremiumFilterSetup, RegionCompare
from steam_radar.config import Settings
from steam_radar.constants import (
    COMPARISON_CURRENCIES,
    FREE_GAME_LIMIT,
    LANGUAGES,
    MAX_COMPARISON_REGIONS,
    PREMIUM_GAME_LIMIT,
    REGIONS,
)
from steam_radar.db.models import Game, Giveaway, Payment, Plan, PriceSnapshot, User, WatchRule
from steam_radar.db.repositories import get_or_create_game, get_or_create_user, get_user, watch_count
from steam_radar.i18n import text
from steam_radar.services.currency import CurrencyError, CurrencyService
from steam_radar.services.premium_analytics import deal_label_key, price_analytics
from steam_radar.services.pricing import NormalizedPrice, format_money, format_price_card
from steam_radar.services.steam import SteamError, SteamGame, SteamProvider

router = Router(name="user")
PREMIUM_PRICES = {1: 100, 3: 270, 12: 900}
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


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username, message.from_user.full_name
        )
        await session.commit()
        if user.language_code and user.country_code:
            await message.answer(text(user.language_code, "menu"), reply_markup=main_keyboard(user.language_code))
            return
    await state.set_state(Onboarding.language)
    await message.answer(text("ru", "welcome"), reply_markup=language_keyboard())


@router.callback_query(Onboarding.language, F.data.startswith("lang:"))
async def choose_language(callback: CallbackQuery, state: FSMContext) -> None:
    language = callback.data.split(":", 1)[1]
    await state.update_data(language=language)
    await state.set_state(Onboarding.region_group)
    await callback.message.edit_text(
        text(language, "choose_region_group"), reply_markup=region_groups_keyboard(language)
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
        text(language, "choose_region"), reply_markup=region_keyboard(language, group=group)
    )
    await callback.answer()


@router.callback_query(Onboarding.region, F.data == "region_groups")
async def onboarding_region_groups(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    language = data.get("language", "ru")
    await state.set_state(Onboarding.region_group)
    await callback.message.edit_text(
        text(language, "choose_region_group"), reply_markup=region_groups_keyboard(language)
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
    async with session_factory() as session:
        user = await get_or_create_user(
            session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
        )
        user.language_code, user.country_code = language, country
        user.timezone = REGIONS[country].timezone
        await session.commit()
    await state.clear()
    await callback.message.edit_text(
        text(language, "ready", region=text(language, f"region_{country.lower()}")),
        reply_markup=main_keyboard(language),
    )
    await callback.answer()


@router.callback_query(F.data == "ui:close")
async def close_current_message(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass


@router.message(Command("menu"))
async def menu(message: Message, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, message.from_user.id)
    language = user.language_code if user else "ru"
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
    await state.clear()
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    language = user.language_code if user else "ru"
    await callback.message.edit_text(text(language, "menu"), reply_markup=main_keyboard(language))
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
    message: Message, state: FSMContext, session_factory: async_sessionmaker, steam: SteamProvider
) -> None:
    async with session_factory() as session:
        user = await _user(session, message.from_user.id)
    try:
        games = await steam.search(
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
) -> None:
    rule = callback.data.split(":", 1)[1]
    await state.update_data(rule=rule)
    if rule == "any":
        await _save_watch(callback, state, session_factory, steam, min_discount=1)
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
    message: Message, state: FSMContext, session_factory: async_sessionmaker, steam: SteamProvider
) -> None:
    data = await state.get_data()
    try:
        if data["rule"] == "discount":
            value = int(message.text or "")
            if not 1 <= value <= 100:
                raise ValueError
            await _save_watch(message, state, session_factory, steam, min_discount=value)
        else:
            value = Decimal((message.text or "").replace(",", "."))
            if value <= 0:
                raise ValueError
            await _save_watch(message, state, session_factory, steam, max_price=value)
    except (ValueError, InvalidOperation):
        language = await _language(session_factory, message.from_user.id)
        await _replace_panel(message, state, text(language, "invalid_value"), back_keyboard("games", language))


async def _save_watch(
    event: CallbackQuery | Message,
    state: FSMContext,
    session_factory: async_sessionmaker,
    steam: SteamProvider,
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
        await callback.message.edit_text(text(language, "premium_full_info"), reply_markup=premium_keyboard(language))
        await callback.answer(text(language, "premium_required"), show_alert=True)
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
    callback: CallbackQuery, session_factory: async_sessionmaker, steam: SteamProvider, settings: Settings
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
    await callback.message.edit_text(text(language, "info_title"), reply_markup=info_keyboard(language))
    await callback.answer()


@router.callback_query(F.data.startswith("info:"))
async def information_page(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    page = callback.data.split(":", 1)[1]
    language = await _language(session_factory, callback.from_user.id)
    key = {
        "search": "info_search_page",
        "monitor": "info_monitor_page",
        "premium": "info_premium_page",
        "region": "info_region_page",
    }.get(page)
    if key is None:
        await callback.answer(text(language, "ui_error"), show_alert=True)
        return
    await callback.message.edit_text(text(language, key), reply_markup=info_page_keyboard(language))
    await callback.answer()


@router.callback_query(F.data == "settings:quiet")
async def settings_quiet(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
    if not user or not user.is_premium:
        await callback.answer(text(user.language_code if user else "ru", "premium_required"), show_alert=True)
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
        await callback.answer(text(user.language_code if user else "ru", "premium_required"), show_alert=True)
        return
    await callback.message.edit_text(
        _digest_screen(user),
        reply_markup=digest_keyboard(user.language_code, user.daily_digest_enabled, user.weekly_digest_enabled),
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
    enabled = user.daily_digest_enabled if kind == "daily" else user.weekly_digest_enabled
    await callback.message.edit_text(
        text(user.language_code, f"digest_{kind}_title"),
        reply_markup=digest_kind_keyboard(user.language_code, kind, enabled),
    )
    await callback.answer()


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
    await settings_digest(callback, session_factory)


@router.callback_query(F.data == "digest:disable_all")
async def digest_disable_all(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        if not user or not user.is_premium:
            await callback.answer(text(user.language_code if user else "ru", "premium_required"), show_alert=True)
            return
        user.daily_digest_enabled = user.weekly_digest_enabled = False
        await session.commit()
    await settings_digest(callback, session_factory)


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
    await settings_digest(callback, session_factory)


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
    await settings_digest(callback, session_factory)
    await callback.answer()


@router.callback_query(F.data == "menu:premium")
async def premium(callback: CallbackQuery, session_factory: async_sessionmaker, settings: Settings) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        tracked = await watch_count(session, user.id) if user else 0
    content, keyboard = _premium_screen(user, user.language_code, settings.telegram_payment_test_mode, tracked)
    await callback.message.edit_text(content, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "premium:info")
async def premium_info(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    language = await _language(session_factory, callback.from_user.id)
    await callback.message.edit_text(
        text(language, "premium_full_info"), reply_markup=back_keyboard("premium", language)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buy:"))
async def buy_premium(callback: CallbackQuery, bot: Bot, session_factory: async_sessionmaker) -> None:
    _, months, stars = callback.data.split(":")
    language = await _language(session_factory, callback.from_user.id)
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=text(language, "premium_invoice_title", months=months),
        description=text(language, "premium_invoice_description"),
        payload=f"premium:{months}",
        currency="XTR",
        prices=[LabeledPrice(label="Premium", amount=int(stars))],
        reply_markup=invoice_keyboard(language, int(stars)),
    )
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, session_factory: async_sessionmaker) -> None:
    valid = query.currency == "XTR" and _parse_premium_payload(query.invoice_payload, query.total_amount) is not None
    language = await _language(session_factory, query.from_user.id)
    await query.answer(ok=valid, error_message=None if valid else text(language, "payment_invalid"))


@router.message(F.successful_payment)
async def payment_success(message: Message, session_factory: async_sessionmaker, settings: Settings) -> None:
    payment = message.successful_payment
    months = _parse_premium_payload(payment.invoice_payload, payment.total_amount)
    if payment.currency != "XTR" or months is None:
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
                months=months,
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
        # Calendar precision is not required for access checks; a billing month is 30 days here.
        user.plan = Plan.PREMIUM
        user.premium_source = "telegram_stars_test" if settings.telegram_payment_test_mode else "telegram_stars"
        if not user.is_premium:
            user.premium_started_at = datetime.now(UTC)
        user.premium_expired_notified_at = None
        user.premium_until = base + timedelta(days=30 * months)
        await session.commit()
    await message.answer(text(user.language_code, "payment_success", months=months))


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
                "payment_history_item",
                date=p.paid_at.strftime("%d.%m.%Y"),
                stars=p.amount_stars,
                months=p.months,
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
    await callback.message.edit_text(content, reply_markup=premium_keyboard(language))
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
        await callback.answer(text(language, "premium_required"), show_alert=True)
        return
    action = "analytics" if callback.data.endswith("analytics") else "compare"
    content = text(language, f"premium_{action}_choose")
    if not rules:
        content += "\n\n" + text(language, "games_empty")
    await callback.message.edit_text(content, reply_markup=premium_games_keyboard(rules, action, language))
    await callback.answer()


@router.callback_query(F.data.startswith("premium_analytics:"))
async def premium_game_analytics(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    rule_id = int(callback.data.rsplit(":", 1)[1])
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        language = user.language_code if user else "ru"
        rule = await session.scalar(
            select(WatchRule)
            .options(selectinload(WatchRule.game))
            .where(WatchRule.id == rule_id, WatchRule.user_id == getattr(user, "id", 0))
        )
        if not user or not user.is_premium or not rule:
            await callback.answer(text(language, "premium_required"), show_alert=True)
            return
        snapshots = list(
            (
                await session.scalars(
                    select(PriceSnapshot)
                    .where(PriceSnapshot.game_id == rule.game_id, PriceSnapshot.country_code == user.country_code)
                    .order_by(PriceSnapshot.checked_at.desc())
                    .limit(30)
                )
            ).all()
        )
    snapshots.reverse()
    stats = price_analytics(
        [item.final_price for item in snapshots], snapshots[-1].initial_price if snapshots else None
    )
    if not stats or not snapshots:
        content = text(language, "analytics_no_data", game=escape(rule.game.name))
    else:
        currency = snapshots[-1].currency
        if stats.sample_count < 2:
            content = text(
                language,
                "analytics_card_insufficient",
                game=escape(rule.game.name),
                current=format_money(stats.current, currency),
                samples=stats.sample_count,
            )
        else:
            content = text(
                language,
                "analytics_card",
                game=escape(rule.game.name),
                current=format_money(stats.current, currency),
                minimum=format_money(stats.minimum, currency),
                maximum=format_money(stats.maximum, currency),
                average=format_money(stats.average, currency),
                saving=format_money(stats.potential_saving, currency),
                samples=stats.sample_count,
                trend=stats.trend,
                trend_direction=text(language, stats.direction_key),
                score=stats.score,
                rating=text(language, deal_label_key(stats.score, stats.current, stats.minimum)),
            )
    await callback.message.edit_text(content, reply_markup=back_keyboard("premium", language))
    await callback.answer()


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


@router.callback_query(RegionCompare.selecting, F.data == "compare:confirm")
async def premium_region_compare_result(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker,
    steam: SteamProvider,
    currency: CurrencyService,
) -> None:
    data = await state.get_data()
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
    converted: list[tuple[str, object, Decimal]] = []
    rates_info = None
    for code in selected:
        try:
            _, price = await steam.details(
                rule.game.steam_app_id, REGIONS[code].steam_country_code, _steam_language(language)
            )
            if price is None:
                continue
            value, rates_info = await currency.convert(price.final, price.currency, user.comparison_currency)
            converted.append((code, price, value))
        except SteamError as error:
            log.warning(
                "comparison_steam_region_failed",
                telegram_id=callback.from_user.id,
                rule_id=data.get("rule_id"),
                steam_app_id=rule.game.steam_app_id,
                region=code,
                exception_type=type(error).__name__,
            )
            continue
        except CurrencyError as error:
            log.warning(
                "comparison_currency_failed",
                telegram_id=callback.from_user.id,
                rule_id=data.get("rule_id"),
                steam_app_id=rule.game.steam_app_id,
                region=code,
                source_currency=getattr(price, "currency", None),
                target_currency=user.comparison_currency,
                exception_type=type(error).__name__,
            )
            continue
    if not converted or rates_info is None:
        await callback.message.edit_text(
            text(language, "compare_unavailable"), reply_markup=back_keyboard("premium", language)
        )
        await callback.answer()
        await state.clear()
        return
    converted.sort(key=lambda item: item[2])
    cheapest = converted[0][2]
    lines = []
    for index, (code, price, value) in enumerate(converted):
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
    updated = rates_info.updated_at.astimezone(ZoneInfo(user.timezone)).strftime("%d.%m.%Y %H:%M %Z")
    content = text(
        language,
        "compare_result",
        game=escape(rule.game.name),
        items="\n\n".join(lines),
        currency=user.comparison_currency,
        updated=updated,
        stale=text(language, "compare_rates_stale") if rates_info.stale else "",
    )
    await callback.message.edit_text(content, reply_markup=back_keyboard("premium", language))
    await callback.answer()
    await state.clear()


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
            reply_markup=back_keyboard("home", user.language_code),
        )
    elif not items:
        await callback.message.edit_text(
            text(user.language_code, "giveaways_empty")
            + _updated_label(last_updated, user.timezone, user.language_code),
            reply_markup=back_keyboard("home", user.language_code),
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
            reply_markup=back_keyboard("home", user.language_code),
        )
    await callback.answer()


@router.callback_query(F.data == "menu:deals")
async def deals(callback: CallbackQuery, session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        user = await _user(session, callback.from_user.id)
        rows = (
            await session.execute(
                select(
                    Game.name,
                    Game.steam_app_id,
                    PriceSnapshot.final_price,
                    PriceSnapshot.currency,
                    PriceSnapshot.discount_percent,
                )
                .join(WatchRule, WatchRule.game_id == Game.id)
                .join(PriceSnapshot, PriceSnapshot.game_id == Game.id)
                .where(
                    WatchRule.user_id == user.id,
                    PriceSnapshot.country_code == user.country_code,
                    PriceSnapshot.discount_percent > 0,
                )
                .order_by(PriceSnapshot.checked_at.desc())
                .limit(20)
            )
        ).all()
    if not rows:
        await callback.message.edit_text(
            text(user.language_code, "deals_empty"),
            reply_markup=back_keyboard("home", user.language_code),
        )
    else:
        seen: set[int] = set()
        lines = [text(user.language_code, "deals_title")]
        for name, app_id, price, currency, discount in rows:
            if app_id in seen:
                continue
            seen.add(app_id)
            lines.append(
                f'• <a href="https://store.steampowered.com/app/{app_id}">{name}</a> — '
                f"{format_money(price, currency)} (−{discount}%)"
            )
        await callback.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=back_keyboard("home", user.language_code),
        )
    await callback.answer()


@router.error()
async def error_handler(event) -> bool:
    log.error(
        "telegram_update_failed",
        error=str(event.exception),
        exception_type=type(event.exception).__name__,
        exc_info=(type(event.exception), event.exception, event.exception.__traceback__),
    )
    callback = getattr(event.update, "callback_query", None)
    if callback is not None:
        try:
            await callback.answer(text("ru", "ui_error"), show_alert=True)
        except TelegramBadRequest:
            pass
    return True


def _profile_screen(user: User, timezone_name: str) -> tuple[str, InlineKeyboardMarkup]:
    country = user.country_code if user.country_code in REGIONS else "KZ"
    language = user.language_code if user.language_code in LANGUAGES else "ru"
    created_at = getattr(user, "created_at", None) or datetime.now(UTC)
    user_timezone = getattr(user, "timezone", None) or timezone_name
    try:
        zone = ZoneInfo(user_timezone)
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
    user: User | None, language: str, test_mode: bool, tracked: int = 0
) -> tuple[str, InlineKeyboardMarkup]:
    if user and user.is_premium and user.premium_until:
        now = datetime.now(UTC)
        remaining_label = _premium_remaining(language, user.premium_until - now)
        try:
            zone = ZoneInfo(getattr(user, "timezone", "UTC"))
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
        return content, active_premium_keyboard(language)
    content = text(language, "premium")
    if test_mode:
        content += "\n\n" + text(language, "premium_test_label")
    return content, premium_keyboard(language)


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
            local_checked = latest.checked_at.astimezone(ZoneInfo(user.timezone))
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
        zone = ZoneInfo(timezone_name)
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


def _parse_premium_payload(payload: str, amount: int) -> int | None:
    try:
        prefix, raw_months = payload.split(":", 1)
        months = int(raw_months)
    except (ValueError, AttributeError):
        return None
    if prefix != "premium" or PREMIUM_PRICES.get(months) != amount:
        return None
    return months


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
