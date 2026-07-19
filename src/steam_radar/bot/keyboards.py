from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from steam_radar.constants import COMPARISON_CURRENCIES, LANGUAGES, REGION_GROUPS, REGIONS
from steam_radar.db.models import WatchRule
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


def _compact_rows(buttons: list[InlineKeyboardButton], width: int = 3) -> list[list[InlineKeyboardButton]]:
    if any(len(button.text) > 16 for button in buttons):
        width = 2
    return [buttons[index : index + width] for index in range(0, len(buttons), width)]


def region_groups_keyboard(
    language: str = "ru", back: bool = False, prefix: str = "region_group"
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=text(language, f"region_group_{group}"), callback_data=f"{prefix}:{group}")]
        for group in REGION_GROUPS
        if any(region.geo_group == group for region in REGIONS.values())
    ]
    if back:
        rows.append([InlineKeyboardButton(text=text(language, "btn_back"), callback_data="menu:settings")])
    rows.append([close_button(language)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def region_keyboard(
    language: str = "ru", back: bool = False, group: str | None = None, prefix: str = "region"
) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=text(language, f"region_{code.lower()}"), callback_data=f"region:{code}")
        for code, region in REGIONS.items()
        if group is None or region.geo_group == group
    ]
    rows = _compact_rows(buttons)
    if group:
        rows.append([InlineKeyboardButton(text=text(language, "btn_back_groups"), callback_data=f"{prefix}_groups")])
    elif back:
        rows.append([InlineKeyboardButton(text=text(language, "btn_back"), callback_data="menu:settings")])
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
    rows.append([InlineKeyboardButton(text=text(language, "btn_info"), callback_data="menu:info")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def timezone_groups_keyboard(language: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=text(language, f"region_group_{group}"), callback_data=f"timezone_group:{group}")]
        for group in REGION_GROUPS
        if any(region.geo_group == group for region in REGIONS.values())
    ]
    rows.append([InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def timezone_keyboard(language: str, group: str | None = None) -> InlineKeyboardMarkup:
    zones = list(
        dict.fromkeys(region.timezone for region in REGIONS.values() if group is None or region.geo_group == group)
    )
    buttons = [InlineKeyboardButton(text=zone, callback_data=f"timezone:{zone}") for zone in zones]
    rows = _compact_rows(buttons, 2)
    rows.append([InlineKeyboardButton(text=text(language, "btn_back_groups"), callback_data="timezone_groups")])
    rows.append([InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def info_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text(language, "info_search"), callback_data="info:search")],
            [InlineKeyboardButton(text=text(language, "info_monitor"), callback_data="info:monitor")],
            [InlineKeyboardButton(text=text(language, "info_premium"), callback_data="info:premium")],
            [InlineKeyboardButton(text=text(language, "info_region"), callback_data="info:region")],
            [InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")],
        ]
    )


def info_page_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="menu:info")],
            [InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")],
        ]
    )


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
        [InlineKeyboardButton(text=text(language, "btn_delete"), callback_data=f"watch_delete:{rule_id}")],
        [InlineKeyboardButton(text=text(language, "btn_games_back"), callback_data="menu:games")],
        [InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def premium_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=text(language, "premium_period", months=m, stars=s), callback_data=f"buy:{m}:{s}")]
        for m, s in ((1, 100), (3, 270), (12, 900))
    ]
    rows.append([InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")])
    rows.append([InlineKeyboardButton(text=text(language, "btn_premium_info"), callback_data="premium:info")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def invoice_keyboard(language: str, stars: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text(language, "btn_pay_stars", stars=stars), pay=True)],
            [close_button(language)],
        ]
    )


def active_premium_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text(language, "btn_price_analytics"), callback_data="premium:analytics")],
            [InlineKeyboardButton(text=text(language, "btn_region_compare"), callback_data="premium:compare")],
            [InlineKeyboardButton(text=text(language, "btn_manage_subscription"), callback_data="premium:manage")],
            [InlineKeyboardButton(text=text(language, "btn_payment_history"), callback_data="premium:history")],
            [InlineKeyboardButton(text=text(language, "btn_premium_info"), callback_data="premium:info")],
            [InlineKeyboardButton(text=text(language, "btn_menu"), callback_data="menu:home")],
        ]
    )


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
            [InlineKeyboardButton(text=text(language, "btn_back"), callback_data="menu:settings")],
            [close_button(language)],
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
        [close_button(language)],
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


def notification_keyboard(language: str, app_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text(language, "btn_steam"), url=f"https://store.steampowered.com/app/{app_id}"
                )
            ],
            [InlineKeyboardButton(text=text(language, "btn_close"), callback_data="ui:close")],
        ]
    )


def deal_broadcast_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text(language, "btn_deals"), callback_data="menu:deals")],
            [InlineKeyboardButton(text=text(language, "btn_close"), callback_data="ui:close")],
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
