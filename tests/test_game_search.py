from types import SimpleNamespace

import pytest

from steam_radar.services.game_search import (
    GameSearchService,
    _expanded_acronym_query,
    _numeric_title_queries,
    _phonetic_initial_queries,
    _phonetic_word_queries,
    _phrase_initial_query,
    _query_matches_title,
    _rank_results,
    transliterate_query,
)
from steam_radar.services.steam import SteamError, SteamGame
from steam_radar.services.steam_catalog import build_search_text


class Scalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class Session:
    def __init__(self, games):
        self.games = games

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def scalars(self, _statement):
        return Scalars(self.games)


class Factory:
    def __init__(self, games):
        self.games = games

    def __call__(self):
        return Session(self.games)


class Steam:
    def __init__(self, remote=None, fail=False):
        self.remote = remote or []
        self.fail = fail

    @staticmethod
    def extract_app_id(_query):
        return None

    async def search(self, _query, _country, _language):
        if self.fail:
            raise SteamError("unavailable")
        return self.remote

    async def details(self, app_id, _country, _language):
        game = next(game for game in self.remote if game.app_id == app_id)
        return game, None


@pytest.mark.asyncio
async def test_external_steam_result_has_priority_over_local_catalog() -> None:
    local = SimpleNamespace(
        steam_app_id=252490,
        name="Rust",
        header_image=None,
        game_type="game",
        is_free=False,
    )
    remote = SteamGame(1091500, "Cyberpunk 2077", None, "game", False)
    service = GameSearchService(Factory([local]), Steam([remote]))
    results = await service.search("cyberpunk", "PL", "english")
    assert [game.app_id for game in results] == [1091500]


@pytest.mark.asyncio
async def test_fuzzy_local_result_survives_temporary_steam_error() -> None:
    local = SimpleNamespace(
        steam_app_id=252490,
        name="Rust",
        header_image=None,
        game_type="game",
        is_free=False,
    )
    results = await GameSearchService(Factory([local]), Steam(fail=True)).search("rsut", "PL", "english")
    assert results[0].name == "Rust"


@pytest.mark.parametrize(
    ("name", "alias"),
    [
        ("Grand Theft Auto: San Andreas", "gta sa"),
        ("Grand Theft Auto V", "gta5"),
        ("Red Dead Redemption 2", "rdr2"),
        ("Counter-Strike 2", "cs2"),
    ],
)
def test_search_text_generates_common_abbreviations(name: str, alias: str) -> None:
    assert alias in build_search_text(name).split(" | ")


@pytest.mark.asyncio
async def test_rawg_fallback_is_merged_and_deduplicated() -> None:
    gta = SteamGame(1547000, "Grand Theft Auto: San Andreas", None, "game", False)

    class Rawg:
        async def search(self, *_args):
            return [gta]

    results = await GameSearchService(Factory([]), Steam([]), rawg=Rawg()).search("gta sa", "PL", "english")
    assert results == [gta]


@pytest.mark.parametrize(
    ("query", "title"),
    [
        ("rdr2", "Red Dead Redemption 2"),
        ("gtasa", "Grand Theft Auto: San Andreas"),
        ("gta sa", "Grand Theft Auto: San Andreas"),
        ("re2", "Resident Evil 2"),
        ("dmc5", "Devil May Cry 5"),
    ],
)
def test_acronym_relevance_is_generated_from_canonical_title(query: str, title: str) -> None:
    assert _query_matches_title(query, title)
    assert _expanded_acronym_query(query) == " ".join(character for character in query if character != " ")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "title", "app_id"),
    [
        ("rdr2", "Red Dead Redemption 2", 1174180),
        ("gtasa", "Grand Theft Auto: San Andreas – The Definitive Edition", 1547000),
        ("gta sa", "Grand Theft Auto: San Andreas – The Definitive Edition", 1547000),
        ("re2", "Resident Evil 2", 883710),
        ("dmc5", "Devil May Cry 5", 601150),
    ],
)
async def test_spaced_steam_search_resolves_acronyms(query: str, title: str, app_id: int) -> None:
    expected = SteamGame(app_id, title, None, "game", False)

    class AcronymSteam(Steam):
        async def search(self, value, *_args):
            expanded = " ".join(character for character in query if character != " ")
            return [expected] if value == expanded else []

    results = await GameSearchService(Factory([]), AcronymSteam()).search(query, "PL", "english")
    assert results == [expected]


@pytest.mark.asyncio
async def test_itad_canonical_title_is_resolved_and_verified_through_steam() -> None:
    rdr2 = SteamGame(1174180, "Red Dead Redemption 2", None, "game", False)

    class Itad:
        async def search_titles(self, *_args, **_kwargs):
            return ["Run Die Run Again", "Red Dead Redemption 2"]

    class CanonicalSteam(Steam):
        async def search(self, query, *_args):
            return [rdr2] if query == "Red Dead Redemption 2" else []

    results = await GameSearchService(Factory([]), CanonicalSteam([rdr2]), itad=Itad()).search(
        "rdr2", "PL", "english"
    )
    assert results == [rdr2]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("гта са", "gta sa"),
        ("ред дед редемпшен 2", "red ded redempshen 2"),
        ("девил мей край 5", "devil mey kray 5"),
        ("резидент івел 2", "rezident ivel 2"),
    ],
)
def test_cyrillic_query_is_transliterated_without_manual_game_aliases(query: str, expected: str) -> None:
    assert transliterate_query(query) == expected


def test_transliterated_phrase_generates_initial_search() -> None:
    assert _phrase_initial_query(transliterate_query("ред дед редемпшен 2")) == "r d r 2"
    assert "d m c 5" in _phonetic_initial_queries("d m k 5")
    assert "r e 2" in _phonetic_initial_queries("r i 2")


def test_short_cyrillic_word_gets_bounded_phonetic_variants() -> None:
    assert "rust" in _phonetic_word_queries(transliterate_query("раст"))


def test_numeric_title_queries_generate_spaced_and_roman_forms() -> None:
    assert _numeric_title_queries("gta5") == ["gta 5", "gta v"]
    assert _numeric_title_queries(transliterate_query("гта5")) == ["gta 5", "gta v"]


@pytest.mark.asyncio
async def test_cyrillic_gta5_resolves_through_generic_roman_title_variant() -> None:
    gta = SteamGame(3240220, "Grand Theft Auto V Enhanced", None, "game", False)
    vice_city = SteamGame(1546990, "Grand Theft Auto: Vice City – The Definitive Edition", None, "game", False)

    class RomanSteam(Steam):
        async def search(self, query, *_args):
            return [gta, vice_city] if query == "gta v" else []

    results = await GameSearchService(Factory([]), RomanSteam()).search("гта5", "KZ", "russian")
    assert results == [gta]


@pytest.mark.asyncio
async def test_russian_rust_does_not_include_itad_noise_after_exact_steam_match() -> None:
    rust = SteamGame(252490, "Rust", None, "game", False)

    class RustSteam(Steam):
        async def search(self, query, *_args):
            return [rust] if query == "rust" else []

    class NoisyItad:
        async def search_titles(self, *_args, **_kwargs):
            return ["Raster", "Rasta Santa"]

    results = await GameSearchService(Factory([]), RustSteam(), itad=NoisyItad()).search(
        "раст", "KZ", "russian"
    )
    assert results == [rust]


def test_exact_title_is_ranked_above_provider_substring_noise() -> None:
    noise = SteamGame(4346320, "Project T.H.R.U.S.T.", None, "game", False)
    rust = SteamGame(252490, "Rust", None, "game", False)

    assert _rank_results("rust", [noise, rust]) == [rust, noise]


@pytest.mark.asyncio
async def test_cyrillic_acronym_uses_transliterated_steam_search() -> None:
    gta = SteamGame(1547000, "Grand Theft Auto: San Andreas – The Definitive Edition", None, "game", False)

    class CyrillicSteam(Steam):
        async def search(self, query, *_args):
            return [gta] if query == "g t a s a" else []

    results = await GameSearchService(Factory([]), CyrillicSteam()).search("гта са", "KZ", "russian")
    assert results == [gta]
