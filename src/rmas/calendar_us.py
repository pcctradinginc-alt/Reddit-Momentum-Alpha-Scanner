"""US equity-market trading-day calendar. Stdlib only — zero API cost.

Used to skip scheduled runs on weekends and NYSE full-day holidays so CI
minutes, API calls and emails are never spent while the market is closed.

Holiday rules (NYSE full closes):
  New Year's Day, MLK Day, Washington's Birthday, Good Friday, Memorial Day,
  Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas.
Observance: Saturday holidays are observed Friday, Sunday holidays Monday —
except New Year's Day on a Saturday, which NYSE does not observe (the
preceding Dec 31 stays a trading day).
"""

from __future__ import annotations

from datetime import date, timedelta


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th `weekday` (Mon=0) of a month, e.g. 3rd Monday of January."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 19 * m) // 433
    month = (h + m - 7 * n + 90) // 25
    day = (h + m - 7 * n + 33 * month + 19) % 32
    return date(year, month, day)


def _observed(d: date) -> date | None:
    """NYSE observance: Sat -> Fri, Sun -> Mon."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def market_holidays(year: int) -> set[date]:
    out: set[date] = set()

    nyd = date(year, 1, 1)
    if nyd.weekday() == 6:              # Sunday -> observed Monday
        out.add(nyd + timedelta(days=1))
    elif nyd.weekday() != 5:            # Saturday -> NOT observed (NYSE rule)
        out.add(nyd)

    out.add(_nth_weekday(year, 1, 0, 3))      # MLK Day
    out.add(_nth_weekday(year, 2, 0, 3))      # Washington's Birthday
    out.add(_easter(year) - timedelta(days=2))  # Good Friday
    out.add(_last_weekday(year, 5, 0))        # Memorial Day
    if year >= 2022:
        out.add(_observed(date(year, 6, 19)))   # Juneteenth
    out.add(_observed(date(year, 7, 4)))      # Independence Day
    out.add(_nth_weekday(year, 9, 0, 1))      # Labor Day
    out.add(_nth_weekday(year, 11, 3, 4))     # Thanksgiving
    out.add(_observed(date(year, 12, 25)))    # Christmas
    return {d for d in out if d is not None}


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d not in market_holidays(d.year)


def next_trading_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while not is_trading_day(nxt):
        nxt += timedelta(days=1)
    return nxt
