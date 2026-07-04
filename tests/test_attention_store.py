"""Attention store: real discovery history with honest cold-start behavior."""

from datetime import date, timedelta

from rmas.data.attention_store import KEEP_DAYS, MIN_HISTORY_DAYS, AttentionStore


def _store(tmp_path):
    return AttentionStore(path=tmp_path / "attention.json")


def test_cold_start_series_is_flat(tmp_path):
    """Unknown tickers must NOT look like an attention explosion."""
    s = _store(tmp_path)
    today = date(2026, 7, 1)
    s.record(today, {"NVDA": 40})
    series = s.series("NVDA", today, days=30)
    assert len(series) == 30
    assert all(v == 40.0 for v in series)   # flat -> z ~ 0 -> gate stays shut


def test_series_with_history_shows_acceleration(tmp_path):
    s = _store(tmp_path)
    start = date(2026, 6, 1)
    for i in range(10):                      # 10 quiet days
        s.record(start + timedelta(days=i), {"PLTR": 3})
    today = start + timedelta(days=10)
    s.record(today, {"PLTR": 45})            # today explodes
    series = s.series("PLTR", today, days=30)
    assert series[-1] == 45.0
    assert series[-2] == 3.0
    # padded days (before recording started) use the median of history
    assert series[0] == 3.0


def test_min_history_days_boundary(tmp_path):
    s = _store(tmp_path)
    start = date(2026, 6, 1)
    for i in range(MIN_HISTORY_DAYS - 1):
        s.record(start + timedelta(days=i), {"AMD": 5})
    today = start + timedelta(days=MIN_HISTORY_DAYS - 1)
    s.record(today, {"AMD": 99})
    # one day short of enough history -> still flat/conservative
    assert s.series("AMD", today, days=10) == [99.0] * 10


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "attention.json"
    s1 = AttentionStore(path=path)
    s1.record(date(2026, 7, 1), {"GME": 12})
    s1.save()
    s2 = AttentionStore(path=path)
    assert s2.observed_days("GME", date(2026, 7, 2)) == 1


def test_pruning_drops_old_days_and_empty_tickers(tmp_path):
    s = _store(tmp_path)
    old = date(2026, 1, 1)
    s.record(old, {"OLD": 7, "KEEP": 5})
    later = old + timedelta(days=KEEP_DAYS + 10)
    s.record(later, {"KEEP": 6})
    assert s.observed_days("OLD", later + timedelta(days=1)) == 0
    assert s.observed_days("KEEP", later + timedelta(days=1)) == 1


def test_corrupt_file_starts_fresh(tmp_path):
    path = tmp_path / "attention.json"
    path.write_text("{not json")
    s = AttentionStore(path=path)
    assert s.series("X", date(2026, 7, 1), days=5) == [0.0] * 5
