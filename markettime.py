"""US market clock, converted to this PC's local time (works for Saskatchewan, which never
changes its clocks, and for anywhere else). No extra packages needed.

US rules: daylight time ("summer") runs from the 2nd Sunday of March to the 1st Sunday of
November, both at 2:00 a.m. New York time. New York is UTC-4 in summer, UTC-5 in winter.
US stock/ETF market: 9:30 a.m. – 4:00 p.m. New York time, Monday to Friday.
"""
from datetime import date, datetime, time, timedelta, timezone

OPEN_NY, CLOSE_NY = time(9, 30), time(16, 0)


def _nth_sunday(year, month, n):
    d = date(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)      # first Sunday
    return d + timedelta(weeks=n - 1)


def dst_bounds(year):
    """(start, end) dates of US daylight time in `year`."""
    return _nth_sunday(year, 3, 2), _nth_sunday(year, 11, 1)


def ny_offset(day):
    start, end = dst_bounds(day.year)
    return -4 if start <= day < end else -5


def ny_to_local(day, t):
    """New York wall-clock time on `day` → naive datetime on this PC's local clock."""
    aware = datetime.combine(day, t, tzinfo=timezone(timedelta(hours=ny_offset(day))))
    return aware.astimezone().replace(tzinfo=None)


def run_time(day, minutes_after_open=45):
    """Local time of the bot's run on `day` when following US market hours."""
    t = (datetime.combine(day, OPEN_NY) + timedelta(minutes=minutes_after_open)).time()
    return ny_to_local(day, t)


def us_hours_local(day):
    return ny_to_local(day, OPEN_NY), ny_to_local(day, CLOSE_NY)


def season(day=None):
    """('summer'|'winter', date of the next US clock change, name of the season it switches to)."""
    day = day or date.today()
    start, end = dst_bounds(day.year)
    if day < start:
        return "winter", start, "summer"
    if day < end:
        return "summer", end, "winter"
    return "winter", dst_bounds(day.year + 1)[0], "summer"
