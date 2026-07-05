"""Fixes for the two data errors the live doctor run uncovered:
stale off-hours quote spreads and IV level masquerading as IV rank."""

from datetime import date

from rmas.data.iv_store import MIN_OBSERVATIONS, IVStore
from rmas.data.market_alpaca import _is_regular_hours


# ---------------------------------------------------------------- spread fix
def test_regular_hours_quote_accepted():
    # Tuesday 2026-07-07 14:00 UTC = 10:00 ET -> RTH
    assert _is_regular_hours("2026-07-07T14:00:00Z")


def test_off_hours_and_weekend_quotes_rejected():
    assert not _is_regular_hours("2026-07-04T23:59:00Z")   # Saturday
    assert not _is_regular_hours("2026-07-07T09:00:00Z")   # 05:00 ET premarket
    assert not _is_regular_hours("2026-07-07T23:30:00Z")   # 19:30 ET after hours
    assert not _is_regular_hours("")                       # garbage
    assert not _is_regular_hours("not-a-date")


def test_rth_boundaries():
    assert _is_regular_hours("2026-07-07T13:30:00Z")       # 09:30 ET open
    assert not _is_regular_hours("2026-07-07T13:29:00Z")   # 09:29 ET
    assert not _is_regular_hours("2026-07-07T20:00:00Z")   # 16:00 ET close


# ---------------------------------------------------------------- IV rank fix
def test_iv_rank_needs_history(tmp_path):
    s = IVStore(path=tmp_path / "iv.json")
    d = date(2026, 7, 1)
    s.record(d, "NVDA", 0.85)
    assert s.rank("NVDA", 0.85, d) is None       # cold start -> caller neutral


def test_iv_rank_is_percentile_of_own_history(tmp_path):
    from datetime import timedelta

    s = IVStore(path=tmp_path / "iv.json")
    start = date(2026, 5, 1)
    for i in range(MIN_OBSERVATIONS):
        s.record(start + timedelta(days=i), "GME", 0.50 + i * 0.01)  # 0.50..0.69
    today = start + timedelta(days=MIN_OBSERVATIONS)
    assert s.rank("GME", 1.50, today) == 100.0   # above everything
    assert s.rank("GME", 0.10, today) == 0.0     # below everything
    mid = s.rank("GME", 0.60, today)
    assert 40.0 <= mid <= 60.0                   # mid-range IV -> mid rank

    # a 100%-IV meme name is NOT rank 100 if its own IV is always high
    s2 = IVStore(path=tmp_path / "iv2.json")
    for i in range(MIN_OBSERVATIONS):
        s2.record(start + timedelta(days=i), "MEME", 1.0 + (i % 5) * 0.1)
    assert s2.rank("MEME", 1.0, today) <= 25.0


def test_iv_store_roundtrip_and_prune(tmp_path):
    from datetime import timedelta

    path = tmp_path / "iv.json"
    s = IVStore(path=path)
    old = date(2025, 1, 1)
    s.record(old, "AMD", 0.4)
    later = old + timedelta(days=500)
    s.record(later, "AMD", 0.5)                  # prunes the 500-day-old entry
    s.save()
    s2 = IVStore(path=path)
    hist = s2._data["AMD"]
    assert old.isoformat() not in hist
    assert later.isoformat() in hist
    s2.record(later, "AMD", -1.0)                # invalid IV ignored
    assert hist == s2._data["AMD"]
