"""Forward-return logging: log_scan appends idempotently, backfill fills
returns with no lookahead, report() aggregates over complete records.

Hermetic: a deterministic synthetic bars_fn, no network.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from rmas.alpha import forward_log
from rmas.features.regime import Regime, RegimeState
from rmas.pipeline.scan import ScanResult
from rmas.types import Bar, Candidate, GateColor, GateResult, SignalTime, TradePlan


def _gate(score: float) -> GateResult:
    return GateResult(name="g", score=score, color=GateColor.GREEN)


def _candidate(ticker: str) -> Candidate:
    return Candidate(
        ticker=ticker, asof=datetime(2026, 7, 1, tzinfo=timezone.utc),
        signal_time=SignalTime.INTRADAY,
        discovery=_gate(0.8), tradeability=_gate(0.7), timing_risk=_gate(0.6),
        strategy="A_early_momentum_long",
        features={"_raw_rel_strength": 0.5}, meta_probability=0.62, rank_score=0.75,
    )


def _plan(ticker: str, entry: float) -> TradePlan:
    return TradePlan(
        ticker=ticker, strategy="A_early_momentum_long", direction="long",
        instrument="equity", entry=entry, stop=entry * 0.95, targets=[entry * 1.1],
        shares=10, features={"_raw_rel_strength": 0.5},
    )


def _scan_result(asof: datetime, tickers_entries: list[tuple[str, float]]) -> ScanResult:
    cands = [_candidate(t) for t, _ in tickers_entries]
    plans = [_plan(t, e) for t, e in tickers_entries]
    regime = RegimeState(regime=Regime.RISK_ON, size_multiplier=1.0,
                         block_new_longs=False, reasons=[])
    return ScanResult(asof=asof, candidates=cands, plans=plans, regime=regime,
                      rejected={}, reddit_mode="oauth", market_mode="live")


def _bars(start: date, closes: list[float]) -> list[Bar]:
    """One bar per calendar day starting at `start`, closes as given."""
    out = []
    for i, c in enumerate(closes):
        t = datetime.combine(start + timedelta(days=i), datetime.min.time(), tzinfo=timezone.utc)
        out.append(Bar(t=t, open=c, high=c, low=c, close=c, volume=1_000_000))
    return out


# --------------------------------------------------------------------------- #
def test_log_scan_appends_one_record_per_plan(tmp_path):
    path = tmp_path / "forward_log.jsonl"
    res = _scan_result(datetime(2026, 7, 1, tzinfo=timezone.utc),
                       [("AAA", 100.0), ("BBB", 50.0)])

    n = forward_log.log_scan(res, path=path)
    assert n == 2

    lines = path.read_text().splitlines()
    assert len(lines) == 2
    recs = [json.loads(l) for l in lines]
    ids = {r["id"] for r in recs}
    assert ids == {"2026-07-01_AAA", "2026-07-01_BBB"}
    for r in recs:
        assert r["complete"] is False
        assert r["returns"] == {"1": None, "3": None, "5": None, "10": None}
        assert r["meta_probability"] == 0.62
        assert r["discovery"] == 0.8
        assert r["regime"] == "risk_on"


def test_log_scan_is_idempotent_per_day(tmp_path):
    path = tmp_path / "forward_log.jsonl"
    res = _scan_result(datetime(2026, 7, 1, tzinfo=timezone.utc), [("AAA", 100.0)])
    assert forward_log.log_scan(res, path=path) == 1
    assert forward_log.log_scan(res, path=path) == 0     # same day+ticker -> skipped
    assert len(path.read_text().splitlines()) == 1


# --------------------------------------------------------------------------- #
def test_backfill_fills_correct_returns_and_flips_complete(tmp_path):
    path = tmp_path / "forward_log.jsonl"
    entry_date = date(2026, 6, 1)
    rec = {
        "id": "2026-06-01_AAA", "date": entry_date.isoformat(), "ticker": "AAA",
        "strategy": "A", "regime": "risk_on", "entry_ref": 100.0,
        "rank_score": 0.5, "meta_probability": None,
        "discovery": 0.8, "tradeability": 0.7, "timing_risk": 0.6,
        "features": {}, "returns": {"1": None, "3": None, "5": None, "10": None},
        "complete": False,
    }
    path.write_text(json.dumps(rec) + "\n")

    # 6 daily closes starting at the entry date: index0=100 (entry),
    # so horizon 1 -> index1, horizon 3 -> index3, horizon 5 -> index5.
    # horizon 10 has no bar yet -> must stay null, no lookahead.
    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    bars = _bars(entry_date, closes)

    def bars_fn(ticker, lookback_days):
        assert ticker == "AAA"
        return bars

    changed = forward_log.backfill(bars_fn, asof=date(2026, 6, 10), path=path)
    assert changed == 1

    recs = [json.loads(l) for l in path.read_text().splitlines()]
    assert len(recs) == 1
    out = recs[0]
    assert out["returns"]["1"] == round((101.0 - 100.0) / 100.0, 4)
    assert out["returns"]["3"] == round((103.0 - 100.0) / 100.0, 4)
    assert out["returns"]["5"] == round((105.0 - 100.0) / 100.0, 4)
    assert out["returns"]["10"] is None          # no bar that far out yet -> no lookahead
    assert out["complete"] is False              # not all horizons filled


def test_backfill_marks_complete_only_when_all_horizons_present(tmp_path):
    path = tmp_path / "forward_log.jsonl"
    entry_date = date(2026, 6, 1)
    rec = {
        "id": "2026-06-01_AAA", "date": entry_date.isoformat(), "ticker": "AAA",
        "strategy": "A", "regime": "risk_on", "entry_ref": 100.0,
        "rank_score": 0.5, "meta_probability": None,
        "discovery": 0.8, "tradeability": 0.7, "timing_risk": 0.6,
        "features": {}, "returns": {"1": None, "3": None, "5": None, "10": None},
        "complete": False,
    }
    path.write_text(json.dumps(rec) + "\n")

    # enough bars for ALL horizons (need index 10 to exist -> 11 bars)
    closes = [100.0 + i for i in range(15)]
    bars = _bars(entry_date, closes)

    def bars_fn(ticker, lookback_days):
        return bars

    changed = forward_log.backfill(bars_fn, asof=date(2026, 6, 20), path=path)
    assert changed == 1
    out = json.loads(path.read_text().splitlines()[0])
    assert all(v is not None for v in out["returns"].values())
    assert out["complete"] is True

    # a second backfill pass must be a no-op (already complete)
    changed_again = forward_log.backfill(bars_fn, asof=date(2026, 6, 21), path=path)
    assert changed_again == 0


def test_backfill_no_bars_leaves_record_untouched(tmp_path):
    path = tmp_path / "forward_log.jsonl"
    rec = {
        "id": "2026-06-01_AAA", "date": "2026-06-01", "ticker": "AAA",
        "strategy": "A", "regime": "risk_on", "entry_ref": 100.0,
        "rank_score": 0.5, "meta_probability": None,
        "discovery": 0.8, "tradeability": 0.7, "timing_risk": 0.6,
        "features": {}, "returns": {"1": None, "3": None, "5": None, "10": None},
        "complete": False,
    }
    path.write_text(json.dumps(rec) + "\n")

    changed = forward_log.backfill(lambda t, d: [], asof=date(2026, 6, 5), path=path)
    assert changed == 0
    out = json.loads(path.read_text().splitlines()[0])
    assert out["complete"] is False


# --------------------------------------------------------------------------- #
def test_report_aggregates_complete_records(tmp_path):
    path = tmp_path / "forward_log.jsonl"
    complete_rec = {
        "id": "2026-06-01_AAA", "date": "2026-06-01", "ticker": "AAA",
        "strategy": "stratA", "regime": "risk_on", "entry_ref": 100.0,
        "rank_score": 0.5, "meta_probability": 0.6,
        "discovery": 0.8, "tradeability": 0.7, "timing_risk": 0.6,
        "features": {},
        "returns": {"1": 0.01, "3": 0.02, "5": 0.03, "10": 0.05},
        "complete": True,
    }
    incomplete_rec = dict(complete_rec, id="2026-06-02_BBB", ticker="BBB",
                          returns={"1": 0.01, "3": None, "5": None, "10": None},
                          complete=False)
    path.write_text(json.dumps(complete_rec) + "\n" + json.dumps(incomplete_rec) + "\n")

    out = forward_log.report(path=path)
    assert "1 complete" in out
    assert "1 still maturing" in out
    assert "stratA" in out
    assert "risk_on" in out


def test_report_no_records(tmp_path):
    path = tmp_path / "forward_log.jsonl"     # never written
    assert "no records" in forward_log.report(path=path)
