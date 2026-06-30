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
