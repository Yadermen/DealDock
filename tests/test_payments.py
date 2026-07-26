from steam_radar.bot.handlers import PremiumPurchase, _parse_premium_payload
from steam_radar.bot.keyboards import active_premium_keyboard, info_page_keyboard, premium_keyboard


def test_valid_star_tariffs() -> None:
    assert _parse_premium_payload("premium:1", 100) == PremiumPurchase(months=1, days=30, stars=100)
    assert _parse_premium_payload("premium:3", 270) == PremiumPurchase(months=3, days=90, stars=270)
    assert _parse_premium_payload("premium:12", 900) == PremiumPurchase(months=12, days=360, stars=900)


def test_admin_test_tariff_uses_the_same_validated_payload_parser() -> None:
    assert _parse_premium_payload("premium:d1", 1, allow_admin_test=True) == PremiumPurchase(months=0, days=1, stars=1)
    assert _parse_premium_payload("premium:d1", 1) is None
    assert _parse_premium_payload("premium:d1", 2, allow_admin_test=True) is None


def test_wrong_amount_or_payload_is_rejected() -> None:
    assert _parse_premium_payload("premium:1", 1) is None
    assert _parse_premium_payload("premium:2", 100) is None
    assert _parse_premium_payload("other:1", 100) is None
    assert _parse_premium_payload("broken", 100) is None


def _callbacks(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data]


def test_test_tariff_is_visible_only_in_admin_keyboard_variants() -> None:
    builders = (
        lambda admin: premium_keyboard("en", is_admin=admin),
        lambda admin: active_premium_keyboard("en", is_admin=admin),
        lambda admin: info_page_keyboard("en", "premium", is_admin=admin),
    )
    for builder in builders:
        assert "buy:d1:1" not in _callbacks(builder(False))
        assert "buy:d1:1" in _callbacks(builder(True))
