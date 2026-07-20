from decimal import Decimal
from types import SimpleNamespace

from steam_radar.bot.handlers import _sort_deal_entries


def deal(name: str, price: str | None, discount: int, game_id: int, regular: str | None = "100"):
    return SimpleNamespace(
        name=name,
        final_price=Decimal(price) if price is not None else None,
        initial_price=Decimal(regular) if regular is not None else None,
        discount_percent=discount,
        game_id=game_id,
        currency="PLN",
    )


def test_sort_by_discount_uses_lower_price_as_tiebreaker() -> None:
    items = [deal("A", "50", 50, 1), deal("B", "30", 50, 2), deal("C", "10", 20, 3)]
    assert [item.name for item in _sort_deal_entries(items, "discount", {})] == ["B", "A", "C"]


def test_sort_by_price_places_free_first_and_missing_last() -> None:
    items = [deal("Missing", None, 50, 1), deal("Paid", "10", 50, 2), deal("Free", "0", 100, 3)]
    assert [item.name for item in _sort_deal_entries(items, "price", {})] == ["Free", "Paid", "Missing"]


def test_sort_by_value_handles_missing_score() -> None:
    items = [deal("Unknown", "20", 0, 1, None), deal("Good", "20", 80, 2)]
    low = SimpleNamespace(price=Decimal("15"), currency="PLN")
    result = _sort_deal_entries(items, "value", {2: low})
    assert [item.name for item in result] == ["Good", "Unknown"]
