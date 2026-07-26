from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from steam_radar.bot.handlers import _referral_levels, _referral_progress, referral_inline_query
from steam_radar.bot.keyboards import main_keyboard, referrals_keyboard
from steam_radar.constants import REFERRAL_LEVELS
from steam_radar.db.models import Plan, Referral, User
from steam_radar.i18n import TEXTS
from steam_radar.services.referrals import (
    ReferralDashboard,
    ReferralService,
    eligible_referral_levels,
)


class Session:
    def __init__(self, scalar_result=None):
        self.scalar_result = scalar_result
        self.added = []

    async def scalar(self, _statement):
        return self.scalar_result

    def add(self, value):
        self.added.append(value)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class Factory:
    def __init__(self, scalar_result):
        self.scalar_result = scalar_result

    def __call__(self):
        return Session(self.scalar_result)


class Bot:
    def __init__(self):
        self.messages = []

    async def get_me(self):
        return SimpleNamespace(username="DealDockBot")

    async def send_message(self, chat_id, content, **kwargs):
        self.messages.append((chat_id, content, kwargs))


class InlineQuery:
    def __init__(self, code: str = "AbC_123-x", language: str = "en"):
        self.query = f"ref:{code}"
        self.from_user = SimpleNamespace(id=123, language_code=language)
        self.results = None
        self.options = None

    async def answer(self, results, **options):
        self.results = results
        self.options = options


@pytest.mark.asyncio
async def test_new_user_is_bound_to_exactly_one_inviter() -> None:
    inviter = SimpleNamespace(id=1)
    newcomer = User(id=2, telegram_id=22, username=None, display_name=None)
    session = Session(inviter)
    service = ReferralService(SimpleNamespace(), SimpleNamespace())

    assert await service.register_pending(session, newcomer, "valid-code")
    assert newcomer.referred_by_user_id == inviter.id
    assert isinstance(session.added[0], Referral)
    assert session.added[0].status == "pending"

    assert not await service.register_pending(session, newcomer, "other-code")
    assert len(session.added) == 1


@pytest.mark.asyncio
async def test_self_referral_is_rejected() -> None:
    user = User(id=7, telegram_id=77, username=None, display_name=None)
    session = Session(SimpleNamespace(id=7))
    service = ReferralService(SimpleNamespace(), SimpleNamespace())
    assert not await service.register_pending(session, user, "own-code")
    assert not session.added


def test_referral_days_start_or_extend_premium() -> None:
    now = datetime.now(UTC)
    free = User(telegram_id=1, username=None, display_name=None, plan=Plan.FREE, referral_days_earned=0)
    ReferralService._grant_days(free, 3, now)
    assert free.plan == Plan.PREMIUM
    assert free.premium_until == now + timedelta(days=3)
    assert free.referral_days_earned == 3

    ReferralService._grant_days(free, 5, now)
    assert free.premium_until == now + timedelta(days=8)
    assert free.referral_days_earned == 8


def test_milestones_are_centralized_and_awarded_once() -> None:
    assert [(level.active_referrals, level.reward_days) for level in REFERRAL_LEVELS] == [
        (1, 5),
        (5, 10),
        (10, 30),
        (25, 90),
        (50, 365),
    ]
    assert [level.key for level in eligible_referral_levels(10, {"first", "five"})] == ["ten"]


def test_referral_progress_and_navigation_are_localized() -> None:
    dashboard = ReferralDashboard("code", 3, 3, 15, REFERRAL_LEVELS[1])
    for language in ("ru", "en", "pl", "uk"):
        rendered = _referral_progress(language, dashboard)
        assert "3 / 5" in rendered
        levels = _referral_levels(language, dashboard)
        assert "✅" not in levels
        assert "👉" in levels
        assert "None" not in rendered
        main_callbacks = [
            button.callback_data
            for row in main_keyboard(language).inline_keyboard
            for button in row
        ]
        assert "menu:referrals" in main_callbacks
        keyboard = referrals_keyboard(language, "AbC_123-x")
        share = keyboard.inline_keyboard[0][0]
        assert share.url is None
        assert share.switch_inline_query is None
        chosen = share.switch_inline_query_chosen_chat
        assert chosen.query == "ref:AbC_123-x"
        assert chosen.allow_user_chats is True
        assert chosen.allow_group_chats is True
        assert chosen.allow_bot_chats is False
        assert chosen.allow_channel_chats is False


def test_all_referral_strings_exist_in_supported_locales() -> None:
    keys = {key for key in TEXTS["ru"] if key.startswith("referral_") or key == "btn_referrals"}
    for language in ("ru", "en", "pl", "uk"):
        assert keys <= set(TEXTS[language])
        assert all(TEXTS[language][key].strip() for key in keys)


@pytest.mark.asyncio
async def test_inline_invitation_accepts_only_code_owned_by_sender() -> None:
    owner = SimpleNamespace(language_code="pl")
    service = ReferralService(Bot(), Factory(owner))
    assert await service.inline_invitation(123, "AbC_123-x") == (
        "pl",
        "https://t.me/DealDockBot?start=AbC_123-x",
    )

    rejected = ReferralService(Bot(), Factory(None))
    assert await rejected.inline_invitation(999, "AbC_123-x") is None


@pytest.mark.parametrize("language", ["ru", "en", "pl", "uk"])
def test_inline_invitation_text_has_no_long_referral_url(language: str) -> None:
    content = TEXTS[language]["referral_inline_message"]
    assert "https://t.me/" not in content
    assert "start=" not in content
    assert TEXTS[language]["btn_referral_start"]
    assert TEXTS[language]["referral_inline_unavailable_title"]
    assert TEXTS[language]["referral_inline_unavailable_message"]


@pytest.mark.asyncio
async def test_inline_handler_returns_ready_card_with_clickable_deep_link() -> None:
    query = InlineQuery(language="uk")
    service = SimpleNamespace(
        inline_invitation=lambda *_args: None,
    )

    async def invitation(*_args):
        return "uk", "https://t.me/DealDockBot?start=AbC_123-x"

    service.inline_invitation = invitation
    await referral_inline_query(query, service)

    assert len(query.results) == 1
    result = query.results[0]
    assert "https://t.me/" not in result.input_message_content.message_text
    launch = result.reply_markup.inline_keyboard[0][0]
    assert launch.url == "https://t.me/DealDockBot?start=AbC_123-x"
    assert query.options == {"cache_time": 0, "is_personal": True}


@pytest.mark.asyncio
async def test_inline_handler_returns_localized_fallback_instead_of_empty_results() -> None:
    query = InlineQuery(language="pl")

    async def unavailable(*_args):
        return None

    await referral_inline_query(query, SimpleNamespace(inline_invitation=unavailable))
    assert len(query.results) == 1
    assert query.results[0].title == TEXTS["pl"]["referral_inline_unavailable_title"]


@pytest.mark.asyncio
async def test_activation_sends_one_dismissible_notification_to_each_user() -> None:
    bot = Bot()
    service = ReferralService(bot, SimpleNamespace())
    until = datetime(2026, 8, 15, tzinfo=UTC)
    await service._notify_activation(
        (10, "en", until),
        (20, "en", until),
        [REFERRAL_LEVELS[0]],
    )
    assert len(bot.messages) == 2
    inviter = bot.messages[1]
    assert "First referral" in inviter[1]
    assert "+10 Premium days" in inviter[1]
    for _, _, options in bot.messages:
        keyboard = options["reply_markup"]
        assert len(keyboard.inline_keyboard) == 1
        assert keyboard.inline_keyboard[0][0].callback_data == "ui:close"
