from steam_radar.bot.keyboards import main_keyboard, premium_keyboard, profile_keyboard, watch_card_keyboard


def _labels(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def test_main_premium_profile_and_card_switch_language() -> None:
    for factory in (main_keyboard, premium_keyboard, profile_keyboard):
        assert _labels(factory("ru")) != _labels(factory("en"))
        assert _labels(factory("uk")) != _labels(factory("ru"))
        assert _labels(factory("pl")) != _labels(factory("ru"))
    assert any("Open in Steam" in label for label in _labels(watch_card_keyboard(1, 10, "en")))
    assert any("Steam" in label for label in _labels(watch_card_keyboard(1, 10, "uk")))
