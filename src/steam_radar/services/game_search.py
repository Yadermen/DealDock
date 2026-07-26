import asyncio

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from steam_radar.db.models import Game, SteamCatalogApp
from steam_radar.services.itad import IsThereAnyDealProvider
from steam_radar.services.rawg import RawgSearchProvider
from steam_radar.services.steam import SteamError, SteamGame, SteamProvider
from steam_radar.services.steam_catalog import build_search_text


class GameSearchService:
    """Existing Steam search enriched with PostgreSQL pg_trgm matches."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        steam: SteamProvider,
        itad: IsThereAnyDealProvider | None = None,
        rawg: RawgSearchProvider | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.steam = steam
        self.itad = itad
        self.rawg = rawg

    async def search(
        self,
        query: str,
        country: str,
        language: str,
        limit: int = 8,
    ) -> list[SteamGame]:
        normalized = " ".join(query.split()).strip()
        if not normalized:
            return []
        latin_query = transliterate_query(normalized)
        if self.steam.extract_app_id(normalized):
            return await self.steam.search(normalized, country, language)

        steam_error: SteamError | None = None
        try:
            remote = await self.steam.search(normalized, country, language)
        except SteamError as error:
            steam_error = error
            remote = []

        if latin_query != normalized.casefold():
            try:
                remote = self._merge(
                    await self.steam.search(latin_query, country, language),
                    remote,
                    limit=limit,
                )
            except SteamError:
                pass

        matching_query = latin_query or normalized
        for numeric_query in _numeric_title_queries(matching_query):
            try:
                numeric_results = await self.steam.search(numeric_query, country, language)
                remote = self._merge(
                    [
                        game
                        for game in numeric_results
                        if _query_matches_title(matching_query, game.name)
                    ],
                    remote,
                    limit=limit,
                )
            except SteamError:
                continue
        phonetic_matches: list[SteamGame] = []
        if latin_query != normalized.casefold():
            for phonetic_query in _phonetic_word_queries(latin_query):
                try:
                    phonetic_results = await self.steam.search(phonetic_query, country, language)
                    exact = [
                        game
                        for game in phonetic_results
                        if "".join(character for character in game.name.casefold() if character.isalnum())
                        == phonetic_query
                    ]
                    phonetic_matches = self._merge(
                        phonetic_matches,
                        exact
                        or [
                            game
                            for game in phonetic_results
                            if _query_matches_title(phonetic_query, game.name)
                        ],
                        limit=limit,
                    )
                except SteamError:
                    continue
        if phonetic_matches:
            remote = phonetic_matches
        acronym_query = _expanded_acronym_query(matching_query)
        if acronym_query and not phonetic_matches:
            relevance_queries = [matching_query, *_phonetic_word_queries(matching_query)]
            direct_relevant = [
                game
                for game in remote
                if any(_query_matches_title(candidate, game.name) for candidate in relevance_queries)
            ]
            try:
                expanded = await self.steam.search(acronym_query, country, language)
                remote = self._merge(
                    [game for game in expanded if _query_matches_title(matching_query, game.name)],
                    direct_relevant,
                    limit=limit,
                )
            except SteamError:
                remote = direct_relevant

        initials_query = _phrase_initial_query(matching_query)
        if initials_query:
            for candidate_query in _phonetic_initial_queries(initials_query):
                initials_compact = candidate_query.replace(" ", "")
                try:
                    initials_results = await self.steam.search(candidate_query, country, language)
                    remote = self._merge(
                        [
                            game
                            for game in initials_results
                            if _query_matches_title(initials_compact, game.name)
                        ],
                        remote,
                        limit=limit,
                    )
                except SteamError:
                    continue

        # External providers are authoritative. ITAD/RAWG only resolve a fuzzy
        # title; every candidate is converted back to a verified Steam game.
        result = self._merge(_rank_results(matching_query, remote), limit=limit)
        if not result and self.itad:
            result = self._merge(
                result,
                await self._itad_matches(matching_query, country, language, limit),
                limit=limit,
            )
        if not result and self.rawg:
            result = self._merge(result, await self.rawg.search(matching_query, country, language), limit=limit)
        if not result:
            result = await self._local_matches(matching_query, limit)
        if not result and steam_error:
            raise steam_error
        return result

    async def _itad_matches(self, query: str, country: str, language: str, limit: int) -> list[SteamGame]:
        try:
            titles = await self.itad.search_titles(query, limit=min(limit, 5)) if self.itad else []
        except Exception:
            return []
        relevant = [title for title in titles if _query_matches_title(query, title)]
        resolved = await asyncio.gather(
            *(self._resolve_steam_title(title, country, language) for title in relevant[:3]),
            return_exceptions=True,
        )
        return [game for game in resolved if isinstance(game, SteamGame)]

    async def _resolve_steam_title(self, title: str, country: str, language: str) -> SteamGame | None:
        try:
            matches = await self.steam.search(title, country, language)
            if not matches:
                return None
            game, _ = await self.steam.details(matches[0].app_id, country, language)
            return game
        except SteamError:
            return None

    @staticmethod
    def _merge(*groups: list[SteamGame], limit: int) -> list[SteamGame]:
        result: list[SteamGame] = []
        seen: set[int] = set()
        for group in groups:
            for game in group:
                if game.app_id in seen:
                    continue
                seen.add(game.app_id)
                result.append(game)
                if len(result) == limit:
                    return result
        return result

    async def _local_matches(self, query: str, limit: int) -> list[SteamGame]:
        # Catalogue aliases can be short by design, while tracked game names must
        # use a stricter threshold: otherwise `rdr2` is incorrectly ranked as Rust.
        catalog_threshold = 0.18 if len(query) <= 5 else 0.30
        tracked_threshold = 0.42 if len(query) <= 5 else 0.30
        async with self.session_factory() as session:
            catalog_similarity = func.greatest(
                func.similarity(SteamCatalogApp.name, query),
                func.word_similarity(query, SteamCatalogApp.search_text),
            )
            catalog = list(
                (
                    await session.scalars(
                        select(SteamCatalogApp)
                        .where(catalog_similarity >= catalog_threshold)
                        .order_by(
                            case((func.strpos(SteamCatalogApp.search_text, query) > 0, 0), else_=1),
                            catalog_similarity.desc(),
                            SteamCatalogApp.name,
                        )
                        .limit(limit)
                    )
                ).all()
            )
            tracked_similarity = func.greatest(
                func.similarity(Game.name, query),
                func.word_similarity(query, Game.name),
            )
            tracked = list(
                (
                    await session.scalars(
                        select(Game)
                        .where(tracked_similarity >= tracked_threshold)
                        .order_by(tracked_similarity.desc(), Game.name)
                        .limit(limit)
                    )
                ).all()
            )
        return self._merge(
            [SteamGame(game.steam_app_id, game.name, None, "game", False) for game in catalog],
            [
                SteamGame(game.steam_app_id, game.name, game.header_image, game.game_type, game.is_free)
                for game in tracked
            ],
            limit=limit,
        )


def _query_matches_title(query: str, title: str) -> bool:
    normalized = "".join(character for character in query.casefold() if character.isalnum())
    if not normalized:
        return False
    aliases = {
        "".join(character for character in alias if character.isalnum())
        for alias in build_search_text(title).split(" | ")
    }
    title_compact = "".join(character for character in title.casefold() if character.isalnum())
    return (
        normalized in aliases
        or normalized in title_compact
        or _matches_numeric_acronym(normalized, title)
    )


def _rank_results(query: str, games: list[SteamGame]) -> list[SteamGame]:
    """Keep provider relevance, but never rank a substring above an exact title."""
    normalized = "".join(character for character in query.casefold() if character.isalnum())

    def rank(item: tuple[int, SteamGame]) -> tuple[int, int]:
        index, game = item
        title_compact = "".join(character for character in game.name.casefold() if character.isalnum())
        words = {
            "".join(character for character in word.casefold() if character.isalnum())
            for word in game.name.replace("-", " ").split()
        }
        aliases = {
            "".join(character for character in alias.casefold() if character.isalnum())
            for alias in build_search_text(game.name).split(" | ")
        }
        if normalized == title_compact:
            relevance = 0
        elif normalized in words:
            relevance = 1
        elif normalized in aliases:
            relevance = 2
        elif title_compact.startswith(normalized):
            relevance = 3
        else:
            relevance = 4
        return relevance, index

    return [game for _, game in sorted(enumerate(games), key=rank)]


def _matches_numeric_acronym(query: str, title: str) -> bool:
    number = next((value for value in _ROMAN_NUMERALS if query.endswith(value)), None)
    if number is None:
        return False
    stem = query[: -len(number)]
    if len(stem) < 2:
        return False
    words = [
        "".join(character for character in word.casefold() if character.isalnum())
        for word in title.split()
    ]
    words = [word for word in words if word]
    stem_words = words[: len(stem)]
    return (
        len(stem_words) == len(stem)
        and "".join(word[0] for word in stem_words) == stem
        and len(words) > len(stem)
        and words[len(stem)] == _ROMAN_NUMERALS[number]
    )


def _expanded_acronym_query(query: str) -> str | None:
    tokens = query.casefold().split()
    compact = "".join(character for character in query.casefold() if character.isalnum())
    if not 2 <= len(compact) <= 8:
        return None
    if len(tokens) > 1 and any(len(token) > 3 for token in tokens):
        return None
    # Avoid a second request for normal long words; short tokens and tokens with
    # digits are the abbreviation shapes Steam's spaced search understands.
    if len(compact) > 5 and not any(character.isdigit() for character in compact):
        return None
    return " ".join(compact)


def _phrase_initial_query(query: str) -> str | None:
    tokens = ["".join(character for character in token if character.isalnum()) for token in query.casefold().split()]
    tokens = [token for token in tokens if token]
    if len(tokens) < 2 or all(len(token) <= 3 for token in tokens):
        return None
    initials = [token if token.isdigit() else token[0] for token in tokens]
    return " ".join(initials)


def _phonetic_initial_queries(query: str) -> list[str]:
    variants = [query]
    for source, target in ((" k ", " c "), (" i ", " e ")):
        padded = f" {query} "
        if source in padded:
            variants.append(padded.replace(source, target).strip())
    return list(dict.fromkeys(variants))


def _phonetic_word_queries(query: str) -> list[str]:
    if " " in query or not 3 <= len(query) <= 8:
        return []
    variants: list[str] = []
    for source, target in (("a", "u"), ("u", "a"), ("k", "c"), ("i", "e")):
        if source in query:
            variants.append(query.replace(source, target))
    return list(dict.fromkeys(variants))


_ROMAN_NUMERALS = {
    "1": "i",
    "2": "ii",
    "3": "iii",
    "4": "iv",
    "5": "v",
    "6": "vi",
    "7": "vii",
    "8": "viii",
    "9": "ix",
    "10": "x",
}


def _numeric_title_queries(query: str) -> list[str]:
    """Generate title-number forms used by stores without game-specific aliases."""
    compact = "".join(character for character in query.casefold() if character.isalnum())
    variants: list[str] = []
    for number, roman in sorted(_ROMAN_NUMERALS.items(), key=lambda item: len(item[0]), reverse=True):
        if not compact.endswith(number) or len(compact) == len(number):
            continue
        stem = compact[: -len(number)]
        variants.extend((f"{stem} {number}", f"{stem} {roman}"))
        break
    return list(dict.fromkeys(variants))


_CYRILLIC_TO_LATIN = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "ґ": "g",
        "д": "d",
        "е": "e",
        "ё": "yo",
        "є": "ye",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "і": "i",
        "ї": "yi",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "kh",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "shch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
)


def transliterate_query(query: str) -> str:
    return " ".join(query.casefold().translate(_CYRILLIC_TO_LATIN).split())
