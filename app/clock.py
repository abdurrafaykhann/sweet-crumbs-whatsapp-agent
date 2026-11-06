"""The one place that decides what "now" is.
Everything date-dependent (Friday Jummah break, same-day cake cutoff, notice periods, opening hours) calls now().
Set DEMO_NOW=2026-11-06T14:00:00 for a fixed demo clock; empty means the real clock in Pakistan time (UTC+5)."""
import datetime as dt
import os

KARACHI = dt.timezone(dt.timedelta(hours=5))


def now() -> dt.datetime:
    """Naive local (Karachi) datetime."""
    fixed = os.environ.get("DEMO_NOW", "").strip()
    if fixed:
        return dt.datetime.fromisoformat(fixed)
    return dt.datetime.now(KARACHI).replace(tzinfo=None)


def today() -> dt.date:
    return now().date()
