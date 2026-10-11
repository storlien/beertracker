"""Bar-day time utilities with Oslo timezone."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

OSLO_TZ = ZoneInfo("Europe/Oslo")
BAR_DAY_CUTOFF_HOUR = 5


def purchase_to_bar_day(ts: datetime) -> str:
    """Convert a purchase timestamp to a bar-day string (YYYY-MM-DD).

    A bar day runs from 05:00 to 05:00 the next calendar day in Oslo time.
    Purchases before 05:00 roll back to the previous bar day.

    Args:
        ts: Purchase timestamp (naive assumed UTC, or timezone-aware).

    Returns:
        Bar day as ``YYYY-MM-DD``.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ZoneInfo("UTC"))

    # Get naive local (wall-clock) time in Oslo
    oslo_time = ts.astimezone(OSLO_TZ).replace(tzinfo=None)

    if oslo_time.hour < BAR_DAY_CUTOFF_HOUR:
        oslo_time = oslo_time - timedelta(days=1)

    return oslo_time.strftime("%Y-%m-%d")


def current_bar_day() -> str:
    """Return the bar-day string for the current moment in Oslo time.

    A bar day runs from 05:00 to 05:00 the next calendar day.
    """
    now = datetime.now(OSLO_TZ).replace(tzinfo=None)
    if now.hour < BAR_DAY_CUTOFF_HOUR:
        now = now - timedelta(days=1)
    return now.strftime("%Y-%m-%d")
