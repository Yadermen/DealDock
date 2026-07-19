from decimal import Decimal

import pytest

from steam_radar.services.pricing import InvalidPrice, format_money, format_price_card, normalize_steam_price


def test_regular_discount_is_calculated_from_prices() -> None:
    price = normalize_steam_price({"currency": "KZT", "initial": 359900, "final": 179900, "discount_percent": 50})
    assert price.initial == Decimal("3599.00")
    assert price.final == Decimal("1799.00")
    assert price.discount_percent == 50
    assert format_money(price.final, "KZT") == "1 799 KZT"


def test_full_discount_requires_zero_current_price() -> None:
    price = normalize_steam_price({"currency": "KZT", "initial": 359900, "final": 0, "discount_percent": 100})
    card = format_price_card("Test Game", price)
    assert price.discount_percent == 100
    assert "Базовая цена: <b>3 599 KZT</b>" in card
    assert "Текущая цена: <b>0 KZT</b>" in card


def test_steam_full_discount_with_stale_final_is_normalized_to_zero() -> None:
    price = normalize_steam_price({"currency": "KZT", "initial": 359900, "final": 359900, "discount_percent": 100})
    assert price.final == 0
    assert price.discount_percent == 100


def test_invalid_price_is_rejected() -> None:
    with pytest.raises(InvalidPrice):
        normalize_steam_price({"currency": "KZT", "initial": 100, "final": 200})
