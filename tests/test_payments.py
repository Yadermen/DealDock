from steam_radar.bot.handlers import _parse_premium_payload


def test_valid_star_tariffs() -> None:
    assert _parse_premium_payload("premium:1", 100) == 1
    assert _parse_premium_payload("premium:3", 270) == 3
    assert _parse_premium_payload("premium:12", 900) == 12


def test_wrong_amount_or_payload_is_rejected() -> None:
    assert _parse_premium_payload("premium:1", 1) is None
    assert _parse_premium_payload("premium:2", 100) is None
    assert _parse_premium_payload("other:1", 100) is None
    assert _parse_premium_payload("broken", 100) is None
