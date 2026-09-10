"""
Date and time helpers.

The portal stores naive local datetimes. Ghana observes GMT (UTC+0) all year
with no daylight saving, so local time and UTC coincide; this keeps rostered
times, attendance stamps and audit entries directly comparable and readable.
"""
from datetime import date, datetime, time, timedelta


def now():
    """Current local timestamp at second resolution."""
    return datetime.now().replace(microsecond=0)


def today():
    return date.today()


def parse_time(value):
    """Convert an 'HH:MM' string into a time object."""
    if isinstance(value, time):
        return value
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def week_start(reference=None):
    """Monday of the week containing the reference date (defaults to today)."""
    reference = reference or today()
    return reference - timedelta(days=reference.weekday())


def week_end(reference=None):
    return week_start(reference) + timedelta(days=6)


def date_range(start, end):
    """Inclusive list of dates from start to end."""
    span = (end - start).days
    return [start + timedelta(days=offset) for offset in range(span + 1)]


def is_weekend(value):
    return value.weekday() >= 5  # Saturday or Sunday


def humanise_duration(minutes):
    """Render a minute count as '7h 30m'."""
    if minutes is None:
        return "-"
    minutes = int(round(minutes))
    sign = "-" if minutes < 0 else ""
    minutes = abs(minutes)
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{sign}{hours}h {remainder}m"
    if hours:
        return f"{sign}{hours}h"
    return f"{sign}{remainder}m"


# Dates render day-first and numeric throughout the portal, which is the
# convention in Ghana: 03/09/2026, never 09/03/2026 or 3 Sep 2026.
#
# These are the defaults behind the `|dt` and `|d` Jinja filters. Anywhere a
# weekday name is wanted alongside the date, the call site adds it and keeps
# the numeric date - "Monday 14/09/2026", "Mon 14/09" - so the day-first
# reading never varies. If the format ever has to change, change it here and
# grep for "%d/%m" to catch the call sites that spell it out.
DATE_FMT = "%d/%m/%Y"
DATETIME_FMT = "%d/%m/%Y %H:%M"
SHORT_DATE_FMT = "%d/%m"


def format_dt(value, fmt=DATETIME_FMT):
    return value.strftime(fmt) if value else "-"


def format_d(value, fmt=DATE_FMT):
    return value.strftime(fmt) if value else "-"
