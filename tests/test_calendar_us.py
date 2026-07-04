"""US trading-day calendar — the zero-cost CI gate must be right."""

from datetime import date

from rmas.calendar_us import is_trading_day, market_holidays, next_trading_day


def test_weekends_closed():
    assert not is_trading_day(date(2026, 7, 4))   # Saturday
    assert not is_trading_day(date(2026, 7, 5))   # Sunday


def test_independence_day_2026_observed_friday():
    # July 4, 2026 is a Saturday -> NYSE closed Friday July 3.
    assert not is_trading_day(date(2026, 7, 3))
    assert is_trading_day(date(2026, 7, 6))       # following Monday open


def test_fixed_and_floating_holidays_2026():
    hols = market_holidays(2026)
    assert date(2026, 1, 1) in hols               # New Year's Day (Thursday)
    assert date(2026, 1, 19) in hols              # MLK: 3rd Monday of January
    assert date(2026, 2, 16) in hols              # Washington's Birthday
    assert date(2026, 4, 3) in hols               # Good Friday (Easter Apr 5)
    assert date(2026, 5, 25) in hols              # Memorial Day
    assert date(2026, 6, 19) in hols              # Juneteenth (Friday)
    assert date(2026, 9, 7) in hols               # Labor Day
    assert date(2026, 11, 26) in hols             # Thanksgiving
    assert date(2026, 12, 25) in hols             # Christmas


def test_new_years_saturday_not_observed():
    # Jan 1, 2022 was a Saturday: NYSE stayed open Friday Dec 31, 2021.
    assert is_trading_day(date(2021, 12, 31))
    assert date(2021, 12, 31) not in market_holidays(2021)


def test_christmas_observed():
    # Dec 25, 2021 Saturday -> observed Friday Dec 24.
    assert not is_trading_day(date(2021, 12, 24))
    # Dec 25, 2022 Sunday -> observed Monday Dec 26.
    assert not is_trading_day(date(2022, 12, 26))


def test_next_trading_day_skips_weekend_and_holiday():
    # From Thursday July 2, 2026: Fri=observed holiday, Sat/Sun weekend.
    assert next_trading_day(date(2026, 7, 2)) == date(2026, 7, 6)


def test_ordinary_days_open():
    assert is_trading_day(date(2026, 3, 10))      # random Tuesday
    assert is_trading_day(date(2026, 10, 14))     # random Wednesday
