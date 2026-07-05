"""X/StockTwits attention adapter: parsing, alpha-safety contract, stability."""

from datetime import date, datetime, timedelta, timezone

from rmas.config import Secrets
from rmas.data.x_source import XAttentionAdapter, _metrics_from_messages


def _adapter(tmp_path, offline=False):
    return XAttentionAdapter(Secrets(env={}), offline=offline,
                             history_path=tmp_path / "xh.json",
                             watchers_path=tmp_path / "xw.json")


def _page(now, n_fresh=5, n_stale=2, watchers=1000, bullish=2, bearish=1):
    msgs = []
    for i in range(n_fresh):
        senti = ("Bullish" if i < bullish else
                 "Bearish" if i < bullish + bearish else None)
        msgs.append({
            "created_at": (now - timedelta(hours=1 + i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "user": {"id": 100 + i},
            "entities": {"sentiment": {"basic": senti} if senti else None},
        })
    for i in range(n_stale):
        msgs.append({
            "created_at": (now - timedelta(hours=30 + i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "user": {"id": 999},
            "entities": {"sentiment": None},
        })
    return {"symbol": {"symbol": "T", "watchlist_count": watchers}, "messages": msgs}


def test_metrics_aggregation_respects_24h_window():
    now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    m = _metrics_from_messages([_page(now)], now)
    assert m["msgs_24h"] == 5.0                    # stale ones excluded
    assert m["users_24h"] == 5.0
    assert m["watchers"] == 1000.0
    assert m["bullish_pct"] == round(2 / 3 * 100, 1)


def test_metrics_tolerate_garbage():
    now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    pages = [{"symbol": {}, "messages": [{"created_at": "junk", "user": None}]}]
    m = _metrics_from_messages(pages, now)
    assert m["msgs_24h"] == 0.0 and m["bullish_pct"] is None


# ------------------------------------------------------ alpha-safety contract
def test_failure_degrades_to_neutral(tmp_path):
    a = _adapter(tmp_path)
    a._fetch_stream = lambda t: None               # network dead
    assert a.metrics("NVDA") is None
    assert a.attention_z("NVDA", date(2026, 7, 6)) == 0.0
    assert a.watchers_growth("NVDA", date(2026, 7, 6)) == 0.0


def test_offline_matches_legacy_synthetic_value(tmp_path):
    from rmas.data.trends_source import TrendsAdapter

    a = _adapter(tmp_path, offline=True)
    legacy = TrendsAdapter(Secrets(env={}), offline=True)
    # offline pipeline must stay bit-identical to the pre-X integration
    assert a.attention_z("GME") == legacy.x_attention_z("GME")
    assert a.watchers_growth("GME") == 0.0


def test_attention_z_cold_start_then_signal(tmp_path):
    a = _adapter(tmp_path)
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    a._fetch_stream = lambda t: [_page(now, n_fresh=4)]
    d0 = date(2026, 7, 10)
    assert a.attention_z("XYZ", d0) == 0.0         # first day: no history

    # seed 8 quiet days of history directly, then a spike day
    for i in range(8):
        a._history.record(d0 + timedelta(days=i), {"XYZ": 4}, {"XYZ": 4})
    spike_day = d0 + timedelta(days=8)
    a._memo.clear()
    a._fetch_stream = lambda t: [_page(now, n_fresh=25)]
    z = a.attention_z("XYZ", spike_day)
    assert z == 3.0                                # clamped at +3, real signal


def test_watchers_growth_bounded_and_additive_only(tmp_path):
    a = _adapter(tmp_path)
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    d0 = date(2026, 7, 10)
    for i in range(6):
        a._watchers.record(d0 + timedelta(days=i), {"XYZ": 1000})
    today = d0 + timedelta(days=6)

    a._fetch_stream = lambda t: [_page(now, watchers=1050)]   # +5% watchers
    g = a.watchers_growth("XYZ", today)
    assert 0.4 <= g <= 0.6                          # ~0.5 (5% of the 10% cap)

    a._memo.clear()
    a2 = _adapter(tmp_path)
    for i in range(6):
        a2._watchers.record(d0 + timedelta(days=i), {"ABC": 1000})
    a2._fetch_stream = lambda t: [_page(now, watchers=700)]   # watchers DROP
    assert a2.watchers_growth("ABC", today) == 0.0  # never negative -> additive-only


def test_feeder_state_consumed_when_fetch_blocked(tmp_path, monkeypatch):
    """CI case: StockTwits 403s the runner IP, but the local feeder already
    pushed today's numbers into the state files -> real data, real z."""
    monkeypatch.setattr("rmas.data.x_source.time.sleep", lambda s: None)
    a = _adapter(tmp_path)
    d0 = date(2026, 7, 10)
    for i in range(8):                             # feeder history
        a._history.record(d0 + timedelta(days=i), {"XYZ": 5}, {"XYZ": 4})
        a._watchers.record(d0 + timedelta(days=i), {"XYZ": 1000})
    today = d0 + timedelta(days=8)
    a._history.record(today, {"XYZ": 42}, {"XYZ": 30})   # today's feeder data
    a._watchers.record(today, {"XYZ": 1100})

    a._fetch_stream = lambda t: None               # runner IP blocked
    m = a.metrics("XYZ", today)
    assert m is not None and m["msgs_24h"] == 42.0 and m["watchers"] == 1100.0
    assert a.attention_z("XYZ", today) == 3.0      # spike vs quiet history
    assert a.watchers_growth("XYZ", today) == 1.0  # +10% watchers -> full bonus


def test_circuit_breaker_after_three_failures(tmp_path, monkeypatch):
    monkeypatch.setattr("rmas.data.x_source.time.sleep", lambda s: None)
    a = _adapter(tmp_path)
    calls = []
    a._fetch_stream = lambda t: calls.append(t) or None
    for t in ("AA", "BB", "CC", "DD", "EE"):
        assert a.metrics(t) is None
    assert calls == ["AA", "BB", "CC"]              # breaker opened after 3
    assert a._dead


def test_memoized_single_fetch_per_run(tmp_path, monkeypatch):
    monkeypatch.setattr("rmas.data.x_source.time.sleep", lambda s: None)
    a = _adapter(tmp_path)
    calls = []
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    a._fetch_stream = lambda t: calls.append(t) or [_page(now)]
    a.metrics("NVDA", date(2026, 7, 6))
    a.attention_z("NVDA", date(2026, 7, 6))
    a.watchers_growth("NVDA", date(2026, 7, 6))
    assert calls == ["NVDA"]                        # one network hit per ticker/run
