"""Alpha package: options DTE window, feedback loop, regime/earnings exits,
sector-trend filter."""

from datetime import date, datetime, timedelta, timezone

from rmas.config import load_config
from rmas.data.options_tradier import _pick_expiry
from rmas.features.regime import Regime, RegimeState
from rmas.paper.broker import Blotter
from rmas.pipeline.scan import _time_stop_days
from rmas.types import Bar, TradePlan


# ------------------------------------------------------- options DTE window
def test_pick_expiry_prefers_20_45_dte():
    today = date(2026, 7, 6)
    exps = ["2026-07-10", "2026-07-17", "2026-08-07", "2026-09-18"]
    assert _pick_expiry(exps, today) == "2026-08-07"          # 32 DTE


def test_pick_expiry_fallbacks():
    today = date(2026, 7, 6)
    # nothing in window -> first beyond
    assert _pick_expiry(["2026-07-10", "2026-09-18"], today) == "2026-09-18"
    # only near-dated -> furthest available
    assert _pick_expiry(["2026-07-08", "2026-07-10"], today) == "2026-07-10"
    assert _pick_expiry(["junk"], today) is None
    assert _pick_expiry([], today) is None


# ------------------------------------------------------------ feedback loop
def _plan(entry=100.0, stop=95.0, targets=None, ts_days=10) -> TradePlan:
    return TradePlan(ticker="XYZ", strategy="A_early_momentum_long",
                     direction="long", instrument="equity", entry=entry,
                     stop=stop, targets=targets or [107.5, 115.0], shares=10,
                     time_stop_days=ts_days, features={"mention_z_7d": 0.9})


def _bars(seq: list[tuple[float, float, float, float]], start: datetime) -> list[Bar]:
    return [Bar(t=start + timedelta(days=i), open=o, high=h, low=lo, close=c,
                volume=1e6) for i, (o, h, lo, c) in enumerate(seq)]


def test_stop_hit_closes_and_logs_outcome(tmp_path, monkeypatch):
    monkeypatch.setattr("rmas.paper.broker.OUTCOMES", tmp_path / "outcomes.json")
    b = Blotter()
    b.open_from_plan(_plan())
    start = datetime.fromisoformat(b.positions[0].opened_at)
    bars = _bars([(100, 103, 99, 102), (101, 102, 94, 95)], start)
    closed = b.update_open(lambda t, d: bars)
    assert closed == 1
    p = b.positions[0]
    assert p.status == "closed" and p.exit_reason == "stop"
    assert p.exit == 95.0 and p.r_multiple == -1.0
    assert p.mfe == 103 and p.mae == 94

    import json
    outcomes = json.loads((tmp_path / "outcomes.json").read_text())
    assert outcomes[0]["win"] is False
    assert outcomes[0]["features"]["mention_z_7d"] == 0.9
    assert outcomes[0]["mfe_r"] == 0.6                       # (103-100)/5


def test_target_hit_wins(tmp_path, monkeypatch):
    monkeypatch.setattr("rmas.paper.broker.OUTCOMES", tmp_path / "o.json")
    b = Blotter()
    b.open_from_plan(_plan())
    start = datetime.fromisoformat(b.positions[0].opened_at)
    bars = _bars([(100, 104, 99, 103), (104, 116, 103, 114)], start)
    assert b.update_open(lambda t, d: bars) == 1
    p = b.positions[0]
    assert p.exit_reason == "target" and p.exit == 115.0
    assert p.r_multiple == 3.0


def test_time_stop_closes_at_last_close(tmp_path, monkeypatch):
    monkeypatch.setattr("rmas.paper.broker.OUTCOMES", tmp_path / "o.json")
    b = Blotter()
    b.open_from_plan(_plan(ts_days=2))
    p0 = b.positions[0]
    p0.opened_at = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    start = datetime.fromisoformat(p0.opened_at)
    bars = _bars([(100, 102, 99, 101), (101, 103, 100, 102)], start)
    assert b.update_open(lambda t, d: bars) == 1
    assert b.positions[0].exit_reason == "time_stop"
    assert b.positions[0].exit == 102.0


def test_no_duplicate_open_positions(tmp_path):
    b = Blotter()
    assert b.open_from_plan(_plan()) is not None
    assert b.open_from_plan(_plan()) is None                 # already open
    assert len(b.open_positions) == 1


def test_blotter_roundtrip(tmp_path):
    path = tmp_path / "blotter.json"
    b = Blotter()
    b.open_from_plan(_plan())
    b.save(path)
    b2 = Blotter.load(path)
    assert b2.open_positions[0].features == {"mention_z_7d": 0.9}


# --------------------------------------------------- regime/earnings exits
def _state(reg: Regime) -> RegimeState:
    return RegimeState(reg, 1.0, False, [])


def test_time_stop_by_regime():
    cfg = load_config()
    assert _time_stop_days(_state(Regime.RISK_ON), cfg, None) == (10, "")
    assert _time_stop_days(_state(Regime.NEUTRAL), cfg, None) == (6, "")
    assert _time_stop_days(_state(Regime.RISK_OFF), cfg, None) == (2, "")


def test_time_stop_capped_before_earnings():
    cfg = load_config()
    days, note = _time_stop_days(_state(Regime.RISK_ON), cfg, 4)
    assert days == 3 and "BEFORE earnings" in note
    days, note = _time_stop_days(_state(Regime.RISK_ON), cfg, 15)
    assert days == 10 and note == ""                         # far away -> no cap
    days, _ = _time_stop_days(_state(Regime.RISK_ON), cfg, 1)
    assert days == 1                                          # never below 1


# --------------------------------------------------------- sector uptrend
def test_sector_uptrend_flag_in_config():
    cfg = load_config()
    assert cfg.tradeability.gate.get("require_sector_uptrend") is True


def test_options_note_in_plan():
    from rmas.strategies.base import build_trade_plan
    from rmas.strategies.early_momentum import EarlyMomentumLong
    from rmas.strategies.base import StrategyContext
    from rmas.types import Candidate, SignalTime

    cand = Candidate(ticker="XYZ", asof=datetime.now(timezone.utc),
                     signal_time=SignalTime.INTRADAY,
                     features={"_raw_iv_rank": 75.0, "_raw_close": 100.0})
    ctx = StrategyContext(close=100.0, atr=2.0)
    strat = EarlyMomentumLong()
    plan = build_trade_plan(cand, ctx, strat, time_stop_days=8,
                            exit_note="Exit BEFORE earnings in 9d.")
    assert "30-60 DTE" in plan.rationale["options"]
    assert "debit spread" in plan.rationale["options"]        # IV rank 75 > 60
    assert "BEFORE earnings" in plan.rationale["exit"]
    assert plan.features["_raw_iv_rank"] == 75.0
