from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Region:
    id: str
    steam_country_code: str
    currency: str
    timezone: str
    translation_key: str
    price_group: str
    geo_group: str

    @property
    def code(self) -> str:
        return self.steam_country_code


def _region(
    code: str, currency: str, timezone: str, price_group: str | None = None, geo_group: str = "europe"
) -> Region:
    return Region(code, code, currency, timezone, f"region_{code.lower()}", price_group or currency, geo_group)


# Expected currencies were verified against Steam Store appdetails on 2026-07-18.
# The currency returned by Steam remains authoritative and is stored with every price snapshot.
REGIONS: dict[str, Region] = {
    "RU": _region("RU", "RUB", "Europe/Moscow"),
    "BY": _region("BY", "USD", "Europe/Minsk", "USD_CIS"),
    "KZ": _region("KZ", "KZT", "Asia/Almaty", geo_group="asia"),
    "UA": _region("UA", "UAH", "Europe/Kyiv"),
    "AM": _region("AM", "USD", "Asia/Yerevan", "USD_CIS", "asia"),
    "AZ": _region("AZ", "USD", "Asia/Baku", "USD_CIS", "asia"),
    "GE": _region("GE", "USD", "Asia/Tbilisi", "USD_CIS", "asia"),
    "KG": _region("KG", "USD", "Asia/Bishkek", "USD_CIS", "asia"),
    "MD": _region("MD", "USD", "Europe/Chisinau", "USD_CIS"),
    "TJ": _region("TJ", "USD", "Asia/Dushanbe", "USD_CIS", "asia"),
    "TM": _region("TM", "USD", "Asia/Ashgabat", "USD_CIS", "asia"),
    "UZ": _region("UZ", "USD", "Asia/Tashkent", "USD_CIS", "asia"),
    "PL": _region("PL", "PLN", "Europe/Warsaw"),
    "DE": _region("DE", "EUR", "Europe/Berlin"),
    "FR": _region("FR", "EUR", "Europe/Paris"),
    "IT": _region("IT", "EUR", "Europe/Rome"),
    "ES": _region("ES", "EUR", "Europe/Madrid"),
    "CZ": _region("CZ", "EUR", "Europe/Prague"),
    "SK": _region("SK", "EUR", "Europe/Bratislava"),
    "AT": _region("AT", "EUR", "Europe/Vienna"),
    "BE": _region("BE", "EUR", "Europe/Brussels"),
    "NL": _region("NL", "EUR", "Europe/Amsterdam"),
    "SE": _region("SE", "EUR", "Europe/Stockholm"),
    "NO": _region("NO", "NOK", "Europe/Oslo"),
    "FI": _region("FI", "EUR", "Europe/Helsinki"),
    "DK": _region("DK", "EUR", "Europe/Copenhagen"),
    "HU": _region("HU", "EUR", "Europe/Budapest"),
    "RO": _region("RO", "EUR", "Europe/Bucharest"),
    "BG": _region("BG", "EUR", "Europe/Sofia"),
    "LT": _region("LT", "EUR", "Europe/Vilnius"),
    "LV": _region("LV", "EUR", "Europe/Riga"),
    "EE": _region("EE", "EUR", "Europe/Tallinn"),
    "GB": _region("GB", "GBP", "Europe/London"),
    "IE": _region("IE", "EUR", "Europe/Dublin"),
    "PT": _region("PT", "EUR", "Europe/Lisbon"),
    "GR": _region("GR", "EUR", "Europe/Athens"),
    "HR": _region("HR", "EUR", "Europe/Zagreb"),
    "SI": _region("SI", "EUR", "Europe/Ljubljana"),
    "CH": _region("CH", "CHF", "Europe/Zurich"),
    # Steam's MENA-USD pricing region has used USD for Türkiye since November 2023.
    "TR": _region("TR", "USD", "Europe/Istanbul", geo_group="middle_east_africa"),
    "US": _region("US", "USD", "America/New_York", geo_group="americas"),
    "CA": _region("CA", "CAD", "America/Toronto", geo_group="americas"),
    "BR": _region("BR", "BRL", "America/Sao_Paulo", geo_group="americas"),
    "MX": _region("MX", "MXN", "America/Mexico_City", geo_group="americas"),
    "JP": _region("JP", "JPY", "Asia/Tokyo", geo_group="asia"),
    "KR": _region("KR", "KRW", "Asia/Seoul", geo_group="asia"),
    "CN": _region("CN", "CNY", "Asia/Shanghai", geo_group="asia"),
    "IN": _region("IN", "INR", "Asia/Kolkata", geo_group="asia"),
    "AU": _region("AU", "AUD", "Australia/Sydney", geo_group="other"),
    "NZ": _region("NZ", "NZD", "Pacific/Auckland", geo_group="other"),
    "ZA": _region("ZA", "ZAR", "Africa/Johannesburg", geo_group="middle_east_africa"),
    "IL": _region("IL", "ILS", "Asia/Jerusalem", geo_group="middle_east_africa"),
}

TIMEZONES = tuple(dict.fromkeys(region.timezone for region in REGIONS.values()))
REGION_GROUPS = ("europe", "asia", "americas", "middle_east_africa", "other")
COMPARISON_CURRENCIES = ("USD", "EUR", "PLN", "KZT", "UAH")
MAX_COMPARISON_REGIONS = 10
LANGUAGES = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English", "uk": "🇺🇦 Українська", "pl": "🇵🇱 Polski"}
FREE_GAME_LIMIT = 10
PREMIUM_GAME_LIMIT = 100
