"""Time zones: the user types Asia/Karachi time, the database stores UTC.

Rule for the whole project:
    * inside the app and in the database, every time is UTC;
    * only the pages convert, using the helpers below.

APP_TIMEZONE in .env names the display zone (default Asia/Karachi).

Windows has no built-in time zone database, so Python needs the `tzdata`
package (`pip install tzdata`). If it is missing, Asia/Karachi still works
through a fixed UTC+5 fallback (Pakistan has had no daylight saving since 2009).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from publisher.config import get_settings

# What a <input type="datetime-local"> sends, plus two forgiving variants.
_INPUT_FORMATS = ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M")
INPUT_FORMAT = "%Y-%m-%dT%H:%M"  # what we put back into the input box
DISPLAY_FORMAT = "%d %b %Y, %I:%M %p"  # 28 Nov 2026, 01:21 PM


@lru_cache(maxsize=8)
def _zone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "Asia/Karachi":
            return timezone(timedelta(hours=5), "PKT")
        raise


def app_tz() -> tzinfo:
    """The zone people see and type times in."""
    return _zone(get_settings().APP_TIMEZONE)


def as_utc(value: datetime) -> datetime:
    """Any datetime as timezone-aware UTC. A naive value is taken as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def local_input_to_utc(raw: str) -> datetime:
    """'2026-12-01T20:30' typed in Karachi time -> 2026-12-01 15:30 UTC.

    Raises ValueError with a message that is safe to show the user.
    """
    text = (raw or "").strip()
    for fmt in _INPUT_FORMATS:
        try:
            naive = datetime.strptime(text, fmt)
        except ValueError:
            continue
        local = naive.replace(second=0, microsecond=0, tzinfo=app_tz())
        return local.astimezone(timezone.utc)
    raise ValueError("Enter the date and time like 2026-12-01 20:30.")


def utc_to_local(value: datetime) -> datetime:
    """A stored UTC time as Karachi (APP_TIMEZONE) time."""
    return as_utc(value).astimezone(app_tz())


def to_input_value(value: datetime | None) -> str:
    """Fill a datetime-local box: UTC in, local 'YYYY-MM-DDTHH:MM' out."""
    if value is None:
        return ""
    return utc_to_local(value).strftime(INPUT_FORMAT)


def format_local(value: datetime | None) -> str:
    """'01 Dec 2026, 08:30 PM PKT' for showing in a page."""
    if value is None:
        return ""
    local = utc_to_local(value)
    return f"{local.strftime(DISPLAY_FORMAT)} {local.tzname() or ''}".strip()
