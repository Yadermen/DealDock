import re
from datetime import UTC, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC_OFFSET_PATTERN = re.compile(r"^(?P<sign>[+-])(?P<hours>\d{2}):(?P<minutes>\d{2})$")


def normalize_utc_offset(value: str) -> str | None:
    candidate = value.strip().upper().removeprefix("UTC").strip()
    match = UTC_OFFSET_PATTERN.fullmatch(candidate)
    if not match:
        return None
    hours = int(match.group("hours"))
    minutes = int(match.group("minutes"))
    if hours > 14 or minutes > 59 or (hours == 14 and minutes != 0):
        return None
    if hours == 0 and minutes == 0:
        return "UTC"
    return f"UTC{match.group('sign')}{hours:02d}:{minutes:02d}"


def timezone_from_name(value: str | None, fallback: str = "UTC") -> tzinfo:
    name = value or fallback
    if name == "UTC":
        return UTC
    normalized = normalize_utc_offset(name)
    if normalized:
        sign = 1 if normalized[3] == "+" else -1
        hours, minutes = (int(part) for part in normalized[4:].split(":"))
        return timezone(sign * timedelta(hours=hours, minutes=minutes), normalized)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        try:
            return ZoneInfo(fallback)
        except ZoneInfoNotFoundError:
            return UTC
