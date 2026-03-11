"""
time_utils.py — Timezone-aware date/time helpers.

In debug mode, the bot may set `bot.debug_date_offset` (int, days) on the bot
instance. today_local() and now_local() respect that offset so all downstream
logic (streak calc, weekly counts, heatmap, etc.) sees the simulated date.
"""

from datetime import date, datetime, timedelta
import pytz
from bot.database import get_setting


def get_tz() -> pytz.BaseTzInfo:
    tz_name = get_setting("timezone") or "US/Eastern"
    try:
        return pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        return pytz.timezone("US/Eastern")


def _debug_offset() -> int:
    """Return the current debug date offset (days) from the bot instance, or 0."""
    try:
        # Import here to avoid circular imports; only resolves if bot is running
        import bot as bot_module
        # The running bot instance stores itself on the module at startup (see bot.py)
        instance = getattr(bot_module, "_bot_instance", None)
        if instance is not None:
            return getattr(instance, "debug_date_offset", 0)
    except Exception:
        pass
    return 0


def now_local() -> datetime:
    return datetime.now(tz=get_tz()) + timedelta(days=_debug_offset())


def today_local() -> date:
    return now_local().date()


def week_start_for(d: date) -> date:
    """Return the Monday of the week containing d."""
    return d - timedelta(days=d.weekday())


def current_week_start() -> date:
    return week_start_for(today_local())


def challenge_dates() -> tuple[date | None, date | None]:
    """Return (start, end) from settings, or (None, None) if not configured."""
    s = get_setting("challenge_start")
    e = get_setting("challenge_end")
    try:
        start = date.fromisoformat(s) if s else None
        end = date.fromisoformat(e) if e else None
        return start, end
    except ValueError:
        return None, None


def weeks_elapsed(since: date) -> int:
    """Number of full weeks elapsed since the challenge start (min 1)."""
    delta = (today_local() - since).days
    return max(1, delta // 7 + (1 if delta % 7 > 0 else 0))


def all_week_starts(since: date, until: date | None = None) -> list[date]:
    """Return list of all Monday dates from the week of `since` to now (or until)."""
    until = until or today_local()
    starts = []
    ws = week_start_for(since)
    while ws <= week_start_for(until):
        starts.append(ws)
        ws += timedelta(weeks=1)
    return starts