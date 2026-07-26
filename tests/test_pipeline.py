from rmas.alerts.report import render_text
from rmas.config import load_config
from rmas.pipeline.scan import run_scan


def test_scan_runs_offline_end_to_end():
    res = run_scan(offline=True)
    assert res is not None
    assert res.regime is not None
    # never emit more than the daily cap
    cfg = load_config()
    assert len(res.plans) <= cfg.run.get("max_alerts_per_day", 3)


def test_scan_plans_are_risk_defined():
    res = run_scan(offline=True)
    for p in res.plans:
        assert p.entry > 0
        assert p.stop != p.entry
        assert p.shares >= 0
        assert "why_signal" in p.rationale
        assert "exit" in p.rationale


def test_scan_is_deterministic_offline():
    a = run_scan(offline=True)
    b = run_scan(offline=True)
    assert [p.ticker for p in a.plans] == [p.ticker for p in b.plans]


def test_report_renders():
    res = run_scan(offline=True)
    text = render_text(res.plans, res.asof)
    assert "RMAS Alerts" in text


def test_all_green_required_for_plan():
    # every plan must come from an all-green candidate
    res = run_scan(offline=True)
    plan_tickers = {p.ticker for p in res.plans}
    for c in res.candidates:
        if c.ticker in plan_tickers:
            assert c.all_green


# --------------------------------------------------------------------------- #
# Near-miss digest + attention-history maturity (observability for CI tuning)
# --------------------------------------------------------------------------- #
def test_near_misses_populated_and_shaped():
    res = run_scan(offline=True)
    assert isinstance(res.near_misses, list)
    assert len(res.near_misses) <= 5
    scores = [nm["score"] for nm in res.near_misses]
    assert scores == sorted(scores, reverse=True)
    for nm in res.near_misses:
        assert set(nm.keys()) == {
            "ticker", "score", "z7", "ape_z", "authors", "mentions", "blockers",
        }
        assert isinstance(nm["ticker"], str)
        assert isinstance(nm["blockers"], list)
        assert len(nm["blockers"]) <= 2


def test_summary_reports_attention_maturity():
    res = run_scan(offline=True)
    assert "attn=" in res.summary()


def test_attention_stats_zero_offline():
    # offline runs never persist a store, so maturity stats stay at 0/0
    res = run_scan(offline=True)
    assert res.attention_tracked == 0
    assert res.attention_mature == 0


def test_near_miss_lines_format():
    from rmas.pipeline.scan import ScanResult
    from datetime import datetime, timezone

    res = ScanResult(
        asof=datetime(2026, 7, 24, tzinfo=timezone.utc),
        near_misses=[{
            "ticker": "PLTR", "score": 0.54, "z7": 1.31, "ape_z": 0.4,
            "authors": 7, "mentions": 12, "blockers": ["mention_z_7d=1.31<1.5"],
        }],
    )
    lines = res.near_miss_lines()
    assert len(lines) == 1
    line = lines[0]
    assert "PLTR" in line
    assert "score=0.54" in line
    assert "z7=1.31" in line
    assert "ape_z=0.4" in line
    assert "authors=7" in line
    assert "mentions=12" in line
    assert "[mention_z_7d=1.31<1.5]" in line
