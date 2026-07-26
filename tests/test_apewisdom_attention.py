"""ApeWisdom as a real, persisted, cold-start-robust DISCOVERY attention
source: store round-trip + attention_accel_z cold-start bridge -> history."""

from datetime import date, timedelta

from rmas.config import Secrets
from rmas.data.apewisdom_source import ApeWisdomAdapter
from rmas.data.apewisdom_store import KEEP_DAYS, MIN_HISTORY_DAYS, ApeWisdomStore


# ------------------------------------------------------------- store round-trip
def test_store_record_series_roundtrip(tmp_path):
    s = ApeWisdomStore(path=tmp_path / "ape.json")
    start = date(2026, 6, 1)
    for i in range(10):
        s.record(start + timedelta(days=i), {"PLTR": 3})
    today = start + timedelta(days=10)
    s.record(today, {"PLTR": 45})

    series = s.series("PLTR", today, days=30)
    assert series[-1] == 45.0
    assert series[-2] == 3.0
    assert series[0] == 3.0                    # padded with median of history

    s.save()
    s2 = ApeWisdomStore(path=s.path)
    assert s2.observed_days("PLTR", today) == 10


def test_store_cold_start_series_is_flat(tmp_path):
    s = ApeWisdomStore(path=tmp_path / "ape.json")
    today = date(2026, 7, 1)
    s.record(today, {"NVDA": 40})
    series = s.series("NVDA", today, days=30)
    assert len(series) == 30
    assert all(v == 40.0 for v in series)      # <5 days history -> flat, z ~ 0


def test_store_prunes_old_days(tmp_path):
    s = ApeWisdomStore(path=tmp_path / "ape.json")
    old = date(2026, 1, 1)
    s.record(old, {"OLD": 7, "KEEP": 5})
    later = old + timedelta(days=KEEP_DAYS + 10)
    s.record(later, {"KEEP": 6})
    assert s.observed_days("OLD", later + timedelta(days=1)) == 0
    assert s.observed_days("KEEP", later + timedelta(days=1)) == 1


def test_store_corrupt_file_starts_fresh(tmp_path):
    path = tmp_path / "ape.json"
    path.write_text("{not json")
    s = ApeWisdomStore(path=path)
    assert s.series("X", date(2026, 7, 1), days=5) == [0.0] * 5


# ------------------------------------------------------------- attention_accel_z
def _adapter(tmp_path):
    return ApeWisdomAdapter(Secrets(env={}), offline=False,
                            store_path=tmp_path / "ape.json")


def test_accel_z_cold_start_uses_24h_delta(tmp_path, monkeypatch):
    a = _adapter(tmp_path)
    monkeypatch.setattr(a, "top", lambda: {
        "XYZ": {"rank": 10, "mentions": 200, "mentions_24h_ago": 100, "upvotes": 0},
    })
    z = a.attention_accel_z("XYZ", date(2026, 7, 10))
    # cold start (no persisted history yet): bounded delta = 200/100 - 1 = 1.0
    assert z == 1.0


def test_accel_z_cold_start_clamped_to_bounds(tmp_path, monkeypatch):
    a = _adapter(tmp_path)
    monkeypatch.setattr(a, "top", lambda: {
        "XYZ": {"rank": 1, "mentions": 5000, "mentions_24h_ago": 10, "upvotes": 0},
    })
    z = a.attention_accel_z("XYZ", date(2026, 7, 10))
    assert z == 3.0                             # clamped, not the raw 499x ratio


def test_accel_z_uses_history_after_min_days(tmp_path, monkeypatch):
    a = _adapter(tmp_path)
    d0 = date(2026, 7, 1)
    # seed MIN_HISTORY_DAYS quiet prior days directly into the store
    a._store = ApeWisdomStore(path=tmp_path / "ape.json")
    for i in range(MIN_HISTORY_DAYS):
        a._store.record(d0 + timedelta(days=i), {"XYZ": 10})
    a._store.save()

    today = d0 + timedelta(days=MIN_HISTORY_DAYS)
    monkeypatch.setattr(a, "top", lambda: {
        "XYZ": {"rank": 5, "mentions": 500, "mentions_24h_ago": 10, "upvotes": 0},
    })
    z = a.attention_accel_z("XYZ", today)
    # big spike vs 10-per-day history -> pinned at the clamp, real history-based
    assert z == 3.0
    assert a._store.observed_days("XYZ", today) == MIN_HISTORY_DAYS


def test_accel_z_unknown_ticker_is_neutral(tmp_path, monkeypatch):
    a = _adapter(tmp_path)
    monkeypatch.setattr(a, "top", lambda: {})
    assert a.attention_accel_z("NOPE", date(2026, 7, 10)) == 0.0


def test_accel_z_offline_is_neutral(tmp_path):
    a = ApeWisdomAdapter(Secrets(env={}), offline=True, store_path=tmp_path / "ape.json")
    assert a.attention_accel_z("XYZ", date(2026, 7, 10)) == 0.0


def test_mentions_and_24h_ago_helpers(tmp_path, monkeypatch):
    a = _adapter(tmp_path)
    monkeypatch.setattr(a, "top", lambda: {
        "XYZ": {"rank": 5, "mentions": 300, "mentions_24h_ago": 150, "upvotes": 0},
    })
    assert a.mentions("XYZ") == 300
    assert a.mentions_24h_ago("XYZ") == 150
    assert a.mentions("UNKNOWN") == 0
    assert a.mentions_24h_ago("UNKNOWN") == 0
