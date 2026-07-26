import ast
import string
from pathlib import Path

from steam_radar.i18n import TEXTS, text


def test_all_languages_have_identical_nonempty_keys() -> None:
    expected = set(TEXTS["ru"])
    for language, catalog in TEXTS.items():
        assert set(catalog) == expected, language
        assert all(isinstance(value, str) and value.strip() for value in catalog.values())


def test_all_localizations_use_the_same_placeholders() -> None:
    formatter = string.Formatter()
    expected = {
        key: {field for _, field, _, _ in formatter.parse(value) if field} for key, value in TEXTS["ru"].items()
    }
    for language, catalog in TEXTS.items():
        for key, value in catalog.items():
            assert {field for _, field, _, _ in formatter.parse(value) if field} == expected[key], (language, key)


def test_unknown_language_and_key_have_safe_fallback() -> None:
    assert text("unknown", "menu") == TEXTS["ru"]["menu"]
    assert text("en", "key_that_does_not_exist") == TEXTS["ru"]["ui_error"]


def test_polish_catalog_is_complete_and_has_no_cyrillic() -> None:
    assert set(TEXTS["pl"]) == set(TEXTS["ru"])
    assert not any(
        any("А" <= char <= "я" or char in "Ёё" for char in value)
        for key, value in TEXTS["pl"].items()
        if key != "welcome_multilingual"
    )
    assert TEXTS["pl"]["btn_close"] == "✖️ Zamknij"


def test_premium_information_is_safe_readable_html_in_every_supported_language() -> None:
    localized_titles = {
        "ru": "Больше возможностей с Premium",
        "en": "More possibilities with Premium",
        "uk": "Більше можливостей із Premium",
        "pl": "Więcej możliwości z Premium",
    }
    forbidden_terms = ("isthereanydeal", "postgresql", "redis", " api", "worker", "http://", "https://")

    for language, title in localized_titles.items():
        content = text(language, "premium_full_info")
        normalized = content.casefold()

        assert title in content
        assert len(content) <= 4096
        assert content.count("<b>") == content.count("</b>") == 8
        assert "<" not in content.replace("<b>", "").replace("</b>", "")
        assert "━━━━━━━━━━━━━━━━━━" in content
        assert not any(term in normalized for term in forbidden_terms)
        assert not any(value in content for value in ("None", "null", "NaN"))


def test_timezone_chooser_is_localized_without_internal_timezone_terms() -> None:
    expected = {
        "ru": "Выберите часовой пояс",
        "en": "Choose your time zone",
        "pl": "Wybierz strefę czasową",
        "uk": "Виберіть часовий пояс",
    }
    for language, phrase in expected.items():
        content = text(language, "choose_timezone")
        assert phrase in content
        assert "IANA" not in content


def test_expired_callback_message_is_localized() -> None:
    for language in ("ru", "en", "pl", "uk"):
        content = text(language, "callback_expired")
        assert content != TEXTS["ru"]["ui_error"]
        assert content.strip()


def test_user_ui_modules_have_no_hardcoded_cyrillic() -> None:
    root = Path(__file__).parents[1] / "src" / "steam_radar"
    files = [
        root / "bot" / "handlers.py",
        root / "bot" / "admin.py",
        root / "bot" / "keyboards.py",
        root / "services" / "pricing.py",
        root / "services" / "monitor.py",
    ]
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        literals = [
            node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        assert not [value for value in literals if any("А" <= char <= "я" or char in "Ёё" for char in value)], path
