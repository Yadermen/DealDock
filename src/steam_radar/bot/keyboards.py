from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, SwitchInlineQueryChosenChat

from steam_radar.constants import (
    ADMIN_TEST_PREMIUM_CODE,
    ADMIN_TEST_PREMIUM_DAYS,
    ADMIN_TEST_PREMIUM_STARS,
    COMPARISON_CURRENCIES,
    LANGUAGES,
    PREMIUM_PRICES,
    REGION_GROUPS,
    REGIONS,
)
from steam_radar.db.models import GiveawayKind, User, WatchRule
from steam_radar.i18n import text
from steam_radar.services.steam import SteamGame


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"lang:{code}")] for code, label in LANGUAGES.items()
        ]
    )


def close_button(language: str, callback_data: str = "menu:home") -> InlineKeyboardButton:
    """Compatibility helper: user screens always navigate to the main menu."""
    return InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")


def menu_button(language: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")


def dismiss_button(language: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text(language, "btn_close"), callback_data="ui:close")


def dismiss_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[dismiss_button(language)]])


def broadcast_dismiss_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text(language, "btn_broadcast_close"),
                    callback_data="ui:close",
                )
            ]
        ]
    )


def _compact_rows(buttons: list[InlineKeyboardButton], width: int = 3) -> list[list[InlineKeyboardButton]]:
    if any(len(button.text) > 16 for button in buttons):
        width = 2
    return [buttons[index : index + width] for index in range(0, len(buttons), width)]


def region_groups_keyboard(
    language: str = "ru",
    back: bool = False,
    prefix: str = "region_group",
    *,
    onboarding: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=text(language, f"region_group_{group}"), callback_data=f"{prefix}:{group}")]
        for group in REGION_GROUPS
        if any(region.geo_group == group for region in REGIONS.values())
    ]
    if back and not onboarding:
        rows.append([InlineKeyboardButton(text=text(language, "btn_back"), callback_data="menu:settings")])
    if not onboarding:
        rows.append([close_button(language)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def region_keyboard(
    language: str = "ru",
    back: bool = False,
    group: str | None = None,
    prefix: str = "region",
    group_back: bool = True,
    onboarding: bool = False,
) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=text(language, f"region_{code.lower()}"), callback_data=f"region:{code}")
        for code, region in REGIONS.items()
        if group is None or region.geo_group == group
    ]
    rows = _compact_rows(buttons)
    if group and group_back:
        back_key = "btn_back_region_list" if onboarding else "btn_back_groups"
        rows.append([InlineKeyboardButton(text=text(language, back_key), callback_data=f"{prefix}_groups")])
    elif back and not onboarding:
        rows.append([InlineKeyboardButton(text=text(language, "btn_back"), callback_data="menu:settings")])
    if not onboarding:
        rows.append([close_button(language)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profile_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text(language, "btn_change_region"), callback_data="settings:region")],
            [InlineKeyboardButton(text=text(language, "btn_change_language"), callback_data="settings:language")],
            [InlineKeyboardButton(text=text(language, "btn_timezone"), callback_data="settings:timezone")],
            [InlineKeyboardButton(text=text(language, "btn_quiet_hours"), callback_data="settings:quiet")],
            [InlineKeyboardButton(text=text(language, "btn_digest"), callback_data="settings:digest")],
            [InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")],
        ]
    )


def main_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    keys = ("btn_games", "btn_search", "btn_deals", "btn_giveaways", "btn_premium", "btn_profile")
    actions = ("games", "search", "deals", "giveaways", "premium", "settings")
    rows = [
        [
            InlineKeyboardButton(text=text(language, keys[i]), callback_data=f"menu:{actions[i]}"),
            InlineKeyboardButton(text=text(language, keys[i + 1]), callback_data=f"menu:{actions[i + 1]}"),
        ]
        for i in range(0, 6, 2)
    ]
    rows.append([InlineKeyboardButton(text=text(language, "btn_referrals"), callback_data="menu:referrals")])
    rows.append([InlineKeyboardButton(text=text(language, "btn_info"), callback_data="menu:info")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def referrals_keyboard(language: str, referral_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text(language, "btn_referral_share"),
                    switch_inline_query_chosen_chat=SwitchInlineQueryChosenChat(
                        query=f"ref:{referral_code}",
                        allow_user_chats=True,
                        allow_bot_chats=False,
                        allow_group_chats=True,
                        allow_channel_chats=False,
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=text(language, "btn_referral_leaderboard"),
                    callback_data="referral:leaderboard",
                )
            ],
            [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="menu:home")],
        ]
    )


def referral_leaderboard_keyboard(language: str, page: int = 0, pages: int = 1) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(text=text(language, "btn_previous"), callback_data=f"referral:leaderboard:{page - 1}")
        )
    if page + 1 < pages:
        navigation.append(
            InlineKeyboardButton(text=text(language, "btn_next"), callback_data=f"referral:leaderboard:{page + 1}")
        )
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text=text(language, "btn_back"), callback_data="menu:referrals")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def timezone_groups_keyboard(language: str, *, onboarding: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=text(language, f"region_group_{group}"), callback_data=f"timezone_group:{group}")]
        for group in REGION_GROUPS
        if any(region.geo_group == group for region in REGIONS.values())
    ]
    rows.append([InlineKeyboardButton(text=text(language, "timezone_manual_button"), callback_data="timezone:manual")])
    if not onboarding:
        rows.append([InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def timezone_keyboard(language: str, group: str | None = None, *, onboarding: bool = False) -> InlineKeyboardMarkup:
    zones = list(
        dict.fromkeys(region.timezone for region in REGIONS.values() if group is None or region.geo_group == group)
    )
    buttons = [InlineKeyboardButton(text=zone, callback_data=f"timezone:{zone}") for zone in zones]
    rows = _compact_rows(buttons, 2)
    rows.append([InlineKeyboardButton(text=text(language, "timezone_manual_button"), callback_data="timezone:manual")])
    back_key = "btn_back_timezone_list" if onboarding else "btn_back_groups"
    rows.append([InlineKeyboardButton(text=text(language, back_key), callback_data="timezone_groups")])
    if not onboarding:
        rows.append([InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def manual_timezone_keyboard(language: str, *, onboarding: bool = False) -> InlineKeyboardMarkup:
    if onboarding:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=text(language, "btn_back_timezone_list"),
                        callback_data="onboarding:timezone",
                    )
                ]
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text(language, "btn_cancel"),
                    callback_data="settings:timezone",
                )
            ]
        ]
    )


def info_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text(language, "info_search"), callback_data="info:search")],
            [InlineKeyboardButton(text=text(language, "info_discounts"), callback_data="info:discounts")],
            [InlineKeyboardButton(text=text(language, "info_giveaways"), callback_data="info:giveaways")],
            [InlineKeyboardButton(text=text(language, "info_analytics"), callback_data="info:analytics")],
            [InlineKeyboardButton(text=text(language, "info_region"), callback_data="info:region")],
            [InlineKeyboardButton(text=text(language, "info_premium"), callback_data="info:premium")],
            [InlineKeyboardButton(text=text(language, "info_referrals"), callback_data="info:referrals")],
            [InlineKeyboardButton(text=text(language, "info_getting_started"), callback_data="info:start")],
            [InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")],
        ]
    )


def info_page_keyboard(
    language: str,
    page: str | None = None,
    *,
    is_premium: bool = False,
    is_admin: bool = False,
    premium_back: str = "menu:info",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if page in {"search", "start"}:
        rows.extend(
            [
                [InlineKeyboardButton(text=text(language, "info_action_search"), callback_data="menu:search")],
                [InlineKeyboardButton(text=text(language, "info_action_games"), callback_data="menu:games")],
            ]
        )
    elif page == "discounts":
        rows.extend(
            [
                [InlineKeyboardButton(text=text(language, "info_action_discounts"), callback_data="menu:deals")],
                [InlineKeyboardButton(text=text(language, "info_action_games"), callback_data="menu:games")],
                [
                    InlineKeyboardButton(
                        text=text(language, "info_view_premium"), callback_data="info:premium:discounts"
                    )
                ],
            ]
        )
    elif page == "giveaways":
        rows.extend(
            [
                [InlineKeyboardButton(text=text(language, "info_action_giveaways"), callback_data="menu:giveaways")],
                [InlineKeyboardButton(text=text(language, "giveaway_types_button"), callback_data="giveaway:settings")],
                [
                    InlineKeyboardButton(
                        text=text(language, "info_view_premium"), callback_data="info:premium:giveaways"
                    )
                ],
            ]
        )
    elif page == "analytics":
        rows.extend(
            [
                [InlineKeyboardButton(text=text(language, "info_action_choose_game"), callback_data="menu:games")],
                [
                    InlineKeyboardButton(
                        text=text(language, "info_view_premium"), callback_data="info:premium:analytics"
                    )
                ],
            ]
        )
    elif page == "region":
        rows.extend(
            [
                [InlineKeyboardButton(text=text(language, "btn_change_region"), callback_data="settings:region")],
                [
                    InlineKeyboardButton(
                        text=text(language, "info_change_currency"),
                        callback_data="settings:comparison_currency",
                    )
                ],
                [InlineKeyboardButton(text=text(language, "btn_timezone"), callback_data="settings:timezone")],
            ]
        )
    elif page == "premium":
        if is_premium:
            rows.append(
                [InlineKeyboardButton(text=text(language, "btn_manage_subscription"), callback_data="menu:premium")]
            )
        else:
            if is_admin:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=text(
                                language,
                                "premium_test_period",
                                days=ADMIN_TEST_PREMIUM_DAYS,
                                stars=ADMIN_TEST_PREMIUM_STARS,
                            ),
                            callback_data=f"buy:{ADMIN_TEST_PREMIUM_CODE}:{ADMIN_TEST_PREMIUM_STARS}",
                        )
                    ]
                )
            rows.extend(
                [
                    [
                        InlineKeyboardButton(
                            text=text(language, "premium_period", months=months, stars=stars),
                            callback_data=f"buy:{months}:{stars}",
                        )
                    ]
                    for months, stars in PREMIUM_PRICES.items()
                ]
            )
        rows.append([InlineKeyboardButton(text=text(language, "info_compare_plans"), callback_data="info:plans")])
    elif page == "plans":
        if not is_premium:
            months = max(PREMIUM_PRICES)
            rows.append(
                [
                    InlineKeyboardButton(
                        text=text(language, "premium_gate_buy", stars=PREMIUM_PRICES[months]),
                        callback_data=f"buy:{months}:{PREMIUM_PRICES[months]}",
                    )
                ]
            )
        rows.append([InlineKeyboardButton(text=text(language, "info_back_premium"), callback_data="info:premium")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    back_callback = premium_back if page == "premium" else "menu:info"
    rows.append([InlineKeyboardButton(text=text(language, "btn_back"), callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_keyboard(target: str = "home", language: str = "ru") -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=text(language, "btn_menu" if target == "home" else "btn_back"), callback_data=f"menu:{target}"
            )
        ]
    ]
    if target != "home":
        rows.append([close_button(language)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def games_keyboard(games: list[SteamGame], language: str = "ru") -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=game.name[:50], callback_data=f"game:{game.app_id}")] for game in games]
    rows.extend([[InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")]])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rule_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text(language, "btn_any_discount"), callback_data="rule:any")],
            [InlineKeyboardButton(text=text(language, "btn_discount"), callback_data="rule:discount")],
            [InlineKeyboardButton(text=text(language, "btn_price"), callback_data="rule:price")],
            [InlineKeyboardButton(text=text(language, "btn_results"), callback_data="search:results")],
            [InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")],
        ]
    )


def watch_list_keyboard(rules: list[WatchRule], language: str = "ru") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🎮 {rule.game.name[:45]}", callback_data=f"watch:{rule.id}")] for rule in rules
    ]
    rows += [
        [InlineKeyboardButton(text=text(language, "btn_add"), callback_data="menu:search")],
        [InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def watch_card_keyboard(
    rule_id: int, app_id: int, language: str = "ru", notifications_enabled: bool = True, retry_price: bool = False
) -> InlineKeyboardMarkup:
    rows = []
    if retry_price:
        rows.append(
            [InlineKeyboardButton(text=text(language, "btn_retry_price"), callback_data=f"watch_refresh:{rule_id}")]
        )
    rows += [
        [
            InlineKeyboardButton(
                text=text(language, "btn_edit_discount"), callback_data=f"watch_edit_discount:{rule_id}"
            )
        ],
        [InlineKeyboardButton(text=text(language, "btn_edit_price"), callback_data=f"watch_edit_price:{rule_id}")],
        [
            InlineKeyboardButton(
                text=text(language, "btn_notification_conditions"), callback_data=f"watch_filters:{rule_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text=text(language, "btn_notify_on" if notifications_enabled else "btn_notify_off"),
                callback_data=f"watch_notify:{rule_id}",
            )
        ],
        [InlineKeyboardButton(text=text(language, "btn_steam"), url=f"https://store.steampowered.com/app/{app_id}")],
        [
            InlineKeyboardButton(text=text(language, "btn_analytics"), callback_data=f"premium_analytics:{rule_id}"),
            InlineKeyboardButton(text=text(language, "btn_compare"), callback_data=f"premium_compare:{rule_id}"),
        ],
        [InlineKeyboardButton(text=text(language, "btn_delete"), callback_data=f"watch_delete:{rule_id}")],
        [InlineKeyboardButton(text=text(language, "btn_games_back"), callback_data="menu:games")],
        [InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def premium_keyboard(language: str = "ru", *, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=text(language, "premium_period", months=m, stars=s), callback_data=f"buy:{m}:{s}")]
        for m, s in PREMIUM_PRICES.items()
    ]
    if is_admin:
        rows.insert(
            0,
            [
                InlineKeyboardButton(
                    text=text(
                        language,
                        "premium_test_period",
                        days=ADMIN_TEST_PREMIUM_DAYS,
                        stars=ADMIN_TEST_PREMIUM_STARS,
                    ),
                    callback_data=f"buy:{ADMIN_TEST_PREMIUM_CODE}:{ADMIN_TEST_PREMIUM_STARS}",
                )
            ],
        )
    rows.append([menu_button(language)])
    rows.append([InlineKeyboardButton(text=text(language, "btn_premium_info"), callback_data="premium:info")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def premium_info_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=text(language, "premium_period", months=m, stars=s), callback_data=f"buy:{m}:{s}")]
        for m, s in PREMIUM_PRICES.items()
    ]
    rows.append([InlineKeyboardButton(text=text(language, "btn_back"), callback_data="menu:premium")])
    rows.append([menu_button(language)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def premium_info_return_keyboard(language: str, back_callback: str, *, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=text(language, "premium_period", months=m, stars=s), callback_data=f"buy:{m}:{s}")]
        for m, s in PREMIUM_PRICES.items()
    ]
    if is_admin:
        rows.insert(
            0,
            [
                InlineKeyboardButton(
                    text=text(
                        language,
                        "premium_test_period",
                        days=ADMIN_TEST_PREMIUM_DAYS,
                        stars=ADMIN_TEST_PREMIUM_STARS,
                    ),
                    callback_data=f"buy:{ADMIN_TEST_PREMIUM_CODE}:{ADMIN_TEST_PREMIUM_STARS}",
                )
            ],
        )
    rows.append([InlineKeyboardButton(text=text(language, "btn_back"), callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def invoice_keyboard(language: str, stars: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text(language, "btn_pay_stars", stars=stars), pay=True)],
            [dismiss_button(language)],
        ]
    )


def active_premium_keyboard(language: str = "ru", *, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=text(language, "btn_price_analytics"), callback_data="premium:analytics")],
        [InlineKeyboardButton(text=text(language, "btn_region_compare"), callback_data="premium:compare")],
        [InlineKeyboardButton(text=text(language, "btn_manage_subscription"), callback_data="premium:manage")],
        [InlineKeyboardButton(text=text(language, "btn_payment_history"), callback_data="premium:history")],
        [InlineKeyboardButton(text=text(language, "btn_premium_info"), callback_data="premium:info")],
        [menu_button(language)],
    ]
    if is_admin:
        rows.insert(
            0,
            [
                InlineKeyboardButton(
                    text=text(
                        language,
                        "premium_test_period",
                        days=ADMIN_TEST_PREMIUM_DAYS,
                        stars=ADMIN_TEST_PREMIUM_STARS,
                    ),
                    callback_data=f"buy:{ADMIN_TEST_PREMIUM_CODE}:{ADMIN_TEST_PREMIUM_STARS}",
                )
            ],
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def analytics_keyboard(
    language: str,
    rule_id: int,
    app_id: int | None = None,
    *,
    back_callback: str = "menu:home",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text(language, "btn_price_history"),
                    callback_data=f"price_history:{rule_id}:30",
                ),
                InlineKeyboardButton(
                    text=text(language, "btn_compare"),
                    callback_data=f"premium_compare:rule:{rule_id}",
                ),
            ],
            [
                InlineKeyboardButton(text=text(language, "btn_change_goal"), callback_data=f"watch:{rule_id}"),
                *(
                    [
                        InlineKeyboardButton(
                            text=text(language, "btn_steam"),
                            url=f"https://store.steampowered.com/app/{app_id}",
                        )
                    ]
                    if app_id is not None
                    else []
                ),
            ],
            [
                InlineKeyboardButton(
                    text=text(language, "btn_back"),
                    callback_data=back_callback,
                )
            ],
        ]
    )


def price_history_keyboard(language: str, rule_id: int, current: str) -> InlineKeyboardMarkup:
    periods = (("7", "7"), ("30", "30"), ("90", "90"), ("365", "365"), ("all", "all"))
    rows = [
        [
            InlineKeyboardButton(
                text=("✅ " if code == current else "") + text(language, f"history_period_{key}"),
                callback_data=f"price_history:{rule_id}:{code}",
            )
            for code, key in periods[:3]
        ],
        [
            InlineKeyboardButton(
                text=("✅ " if code == current else "") + text(language, f"history_period_{key}"),
                callback_data=f"price_history:{rule_id}:{code}",
            )
            for code, key in periods[3:]
        ],
        [InlineKeyboardButton(text=text(language, "btn_back"), callback_data=f"premium_analytics:{rule_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def giveaways_keyboard(user: User, language: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=text(
                    language,
                    "giveaway_notifications_button",
                    state=text(language, "enabled" if user.giveaway_notifications_enabled else "disabled"),
                ),
                callback_data="giveaway:toggle",
            )
        ]
    ]
    rows.append([InlineKeyboardButton(text=text(language, "giveaway_types_button"), callback_data="giveaway:settings")])
    rows.append([InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def giveaway_types_keyboard(user: User, language: str) -> InlineKeyboardMarkup:
    selected = set(user.giveaway_notification_kinds or [])
    rows = [
        [
            InlineKeyboardButton(
                text=("✅ " if kind.value in selected else "▫️ ") + text(language, f"giveaway_{kind.value}"),
                callback_data=f"giveaway_kind:{kind.value}",
            )
        ]
        for kind in (GiveawayKind.KEEP, GiveawayKind.WEEKEND, GiveawayKind.DLC)
    ]
    rows.append(
        [
            InlineKeyboardButton(text=text(language, "btn_select_all"), callback_data="giveaway_kinds:all"),
            InlineKeyboardButton(text=text(language, "btn_disable_all"), callback_data="giveaway_kinds:none"),
        ]
    )
    rows.append([InlineKeyboardButton(text=text(language, "btn_back"), callback_data="menu:giveaways")])
    rows.append([menu_button(language)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def deals_keyboard(
    language: str,
    page: int,
    pages: int,
    sort: str,
    analytics_items: list[tuple[int, str]] | None = None,
) -> InlineKeyboardMarkup:
    rows = []
    rows.append(
        [
            InlineKeyboardButton(text=text(language, "deals_sort_open"), callback_data="deals:sort_screen"),
            InlineKeyboardButton(text=text(language, "deals_filters_open"), callback_data="deals:filter_screen"),
        ]
    )
    if analytics_items:
        if len(analytics_items) == 1:
            rule_id, name = analytics_items[0]
            label = text(language, "deals_analytics_named", name=_short_button_name(name))
            rows.append([InlineKeyboardButton(text=label, callback_data=f"deals_analytics:{rule_id}")])
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=text(language, "deals_analytics_all"), callback_data="deals:analytics_screen"
                    )
                ]
            )
    navigation = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(text=text(language, "btn_previous"), callback_data=f"deals_page:{page - 1}")
        )
    if page + 1 < pages:
        navigation.append(InlineKeyboardButton(text=text(language, "btn_next"), callback_data=f"deals_page:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append([menu_button(language)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def deals_sort_keyboard(language: str, active: str) -> InlineKeyboardMarkup:
    options = (
        ("discount", "deals_sort_discount"),
        ("price", "deals_sort_price"),
        ("value", "deals_sort_value"),
        ("name", "deals_sort_name"),
    )
    rows = [
        [
            InlineKeyboardButton(
                text=("✅ " if active == mode else "") + text(language, key), callback_data=f"deals_sort:{mode}"
            )
        ]
        for mode, key in options
    ]
    rows.append([InlineKeyboardButton(text=text(language, "btn_back"), callback_data="deals:return")])
    rows.append([menu_button(language)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def deals_filters_keyboard(language: str, filters: dict) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=text(language, "deals_filter_discount_input"),
                callback_data="deals_filter:discount",
            ),
            InlineKeyboardButton(
                text=text(language, "deals_filter_price_input"),
                callback_data="deals_filter:max_price",
            ),
        ],
        [
            InlineKeyboardButton(
                text="✅ " + text(language, "deals_filter_tracked"),
                callback_data="deals_filter:tracked",
            )
        ],
        [
            InlineKeyboardButton(
                text=("✅ " if filters.get("historical_low_only") else "▫️ ")
                + text(language, "deals_filter_historical"),
                callback_data="deals_filter:historical",
            ),
            InlineKeyboardButton(
                text=("✅ " if filters.get("free_only") else "▫️ ") + text(language, "deals_filter_free"),
                callback_data="deals_filter:free",
            ),
        ],
        [InlineKeyboardButton(text=text(language, "deals_filter_reset"), callback_data="deals_filter:reset")],
        [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="deals:return")],
        [menu_button(language)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def deals_filter_input_keyboard(language: str, kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text(language, "deals_filter_remove"),
                    callback_data=f"deals_filter_clear:{kind}",
                )
            ],
            [InlineKeyboardButton(text=text(language, "btn_cancel"), callback_data="deals:filter_screen")],
        ]
    )


def deals_analytics_keyboard(language: str, items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=_short_button_name(name), callback_data=f"deals_analytics:{rule_id}")]
        for rule_id, name in items
    ]
    rows.append([InlineKeyboardButton(text=text(language, "btn_back"), callback_data="deals:return")])
    rows.append([menu_button(language)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def premium_gate_keyboard(language: str, back_callback: str, months: int, stars: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text(language, "premium_gate_buy", stars=stars),
                    callback_data=f"buy:{months}:{stars}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=text(language, "premium_gate_more"),
                    callback_data=f"premium:info:{back_callback}"[:64],
                )
            ],
            [InlineKeyboardButton(text=text(language, "btn_back"), callback_data=back_callback)],
        ]
    )


def _short_button_name(name: str, limit: int = 42) -> str:
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "…"


def comparison_currency_keyboard(language: str, current: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=("✅ " if code == current else "") + code, callback_data=f"compare_currency:{code}")
        for code in COMPARISON_CURRENCIES
    ]
    rows = _compact_rows(buttons, 3)
    rows += [
        [InlineKeyboardButton(text=text(language, "btn_back_groups"), callback_data="compare:groups")],
        [InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profile_currency_keyboard(language: str, current: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=("✅ " if code == current else "") + code,
            callback_data=f"settings_currency:{code}",
        )
        for code in COMPARISON_CURRENCIES
    ]
    rows = _compact_rows(buttons)
    rows.append([InlineKeyboardButton(text=text(language, "btn_back"), callback_data="info:region")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def comparison_groups_keyboard(language: str, selected_count: int, currency: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=text(language, f"region_group_{group}"), callback_data=f"compare_group:{group}")]
        for group in REGION_GROUPS
        if any(region.geo_group == group for region in REGIONS.values())
    ]
    rows += [
        [
            InlineKeyboardButton(
                text=text(language, "compare_base_currency", currency=currency), callback_data="compare:currency"
            )
        ],
        [InlineKeyboardButton(text=text(language, "compare_clear"), callback_data="compare:clear")],
        [
            InlineKeyboardButton(
                text=text(language, "compare_confirm", count=selected_count), callback_data="compare:confirm"
            )
        ],
        [InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def comparison_regions_keyboard(language: str, group: str, selected: set[str]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=("✅ " if code in selected else "▫️ ") + text(language, region.translation_key),
            callback_data=f"compare_toggle:{code}",
        )
        for code, region in REGIONS.items()
        if region.geo_group == group
    ]
    rows = _compact_rows(buttons, 3)
    rows += [
        [InlineKeyboardButton(text=text(language, "compare_select_group"), callback_data=f"compare_all:{group}")],
        [InlineKeyboardButton(text=text(language, "btn_back_groups"), callback_data="compare:groups")],
        [InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def comparison_result_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text(language, "btn_refresh_prices"), callback_data="compare:refresh")],
            [InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")],
        ]
    )


def quiet_hours_keyboard(language: str, enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text(language, "quiet_turn_off" if enabled else "quiet_turn_on"), callback_data="quiet:toggle"
                )
            ],
            [InlineKeyboardButton(text=text(language, "quiet_change_start"), callback_data="quiet:start")],
            [InlineKeyboardButton(text=text(language, "quiet_change_end"), callback_data="quiet:end")],
            [InlineKeyboardButton(text=text(language, "btn_timezone"), callback_data="settings:timezone")],
            [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="menu:settings")],
            [close_button(language)],
        ]
    )


def hour_keyboard(language: str, prefix: str, back_callback: str = "settings:quiet") -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=f"{hour:02d}:00", callback_data=f"{prefix}_hour:{hour}")
            for hour in range(start, start + 4)
        ]
        for start in range(0, 24, 4)
    ]
    rows += [
        [InlineKeyboardButton(text=text(language, "btn_back"), callback_data=back_callback)],
        [close_button(language)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def minute_keyboard(
    language: str, prefix: str, hour: int, back_callback: str = "settings:quiet"
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"{hour:02d}:{minute:02d}", callback_data=f"{prefix}_minute:{hour}:{minute}")
                for minute in (0, 15)
            ],
            [
                InlineKeyboardButton(text=f"{hour:02d}:{minute:02d}", callback_data=f"{prefix}_minute:{hour}:{minute}")
                for minute in (30, 45)
            ],
            [InlineKeyboardButton(text=text(language, "btn_back"), callback_data=back_callback)],
            [close_button(language)],
        ]
    )


def digest_keyboard(language: str, daily: bool, weekly: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text(language, "digest_daily_settings"), callback_data="digest:daily")],
            [InlineKeyboardButton(text=text(language, "digest_weekly_settings"), callback_data="digest:weekly")],
            [InlineKeyboardButton(text=text(language, "digest_disable_all"), callback_data="digest:disable_all")],
            [InlineKeyboardButton(text=text(language, "digest_guide_button"), callback_data="digest:guide")],
            [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="menu:settings")],
            [menu_button(language)],
        ]
    )


def digest_kind_keyboard(language: str, kind: str, enabled: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=text(language, "digest_turn_off" if enabled else "digest_turn_on"),
                callback_data=f"digest_toggle:{kind}",
            )
        ],
        [InlineKeyboardButton(text=text(language, "digest_change_time"), callback_data=f"digest_time:{kind}")],
    ]
    if kind == "weekly":
        rows.append(
            [InlineKeyboardButton(text=text(language, "digest_change_weekday"), callback_data="digest:weekday")]
        )
    rows += [
        [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="settings:digest")],
        [menu_button(language)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def weekday_keyboard(language: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=text(language, f"weekday_{day}"), callback_data=f"digest_weekday:{day}")]
        for day in range(7)
    ]
    rows += [
        [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="digest:weekly")],
        [close_button(language)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def premium_games_keyboard(rules: list[WatchRule], action: str, language: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=rule.game.name[:50],
                callback_data=(
                    f"premium_compare:rule:{rule.id}" if action == "compare" else f"premium_{action}:{rule.id}"
                ),
            )
        ]
        for rule in rules
    ]
    rows.append([InlineKeyboardButton(text=text(language, "btn_back"), callback_data="menu:premium")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def notification_keyboard(
    language: str, app_id: int, rule_id: int | None = None, premium: bool = False
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text(language, "btn_steam"),
                    url=f"https://store.steampowered.com/app/{app_id}",
                )
            ],
            [InlineKeyboardButton(text=text(language, "btn_close"), callback_data="ui:close")],
        ]
    )


def deal_broadcast_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text(language, "btn_deals"), callback_data="menu:deals")],
            [InlineKeyboardButton(text=text(language, "btn_broadcast_close"), callback_data="ui:close")],
        ]
    )


def premium_filters_keyboard(rule: WatchRule, language: str) -> InlineKeyboardMarkup:
    def mark(value: bool) -> str:
        return "✅" if value else "▫️"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text(language, "filter_max_price"), callback_data=f"filter_value:max_price:{rule.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=text(language, "filter_min_discount"), callback_data=f"filter_value:min_discount:{rule.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{mark(rule.notify_on_new_historical_low)} {text(language, 'filter_new_low')}",
                    callback_data=f"filter_toggle:new_low:{rule.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{mark(rule.notify_on_known_historical_low)} {text(language, 'filter_known_low')}",
                    callback_data=f"filter_toggle:known_low:{rule.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=text(language, "filter_drop_amount"), callback_data=f"filter_value:drop_amount:{rule.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=text(language, "filter_drop_percent"), callback_data=f"filter_value:drop_percent:{rule.id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{mark(rule.notify_on_any_price_drop)} {text(language, 'filter_any_drop')}",
                    callback_data=f"filter_toggle:any_drop:{rule.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=text(
                        language, "filter_repeat", policy=text(language, f"repeat_{rule.repeat_notification_policy}")
                    ),
                    callback_data=f"filter_repeat:{rule.id}",
                )
            ],
            [InlineKeyboardButton(text=text(language, "btn_games_back"), callback_data=f"watch:{rule.id}")],
            [InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")],
        ]
    )
