"""Daily heartbeat: an actionable 0-setup day must still email a liveness
confirmation when run.daily_heartbeat is on, so a healthy quiet engine never
looks identical, from the inbox, to a dead cron job. Degraded (synthetic)
runs and cli --dry-run must still never bypass their existing guards."""

from __future__ import annotations

from rmas.cli import decide_dry_run


def test_actionable_zero_plans_heartbeat_on_sends():
    dry, reason = decide_dry_run(actionable=True, n_plans=0, email_when_no_setups=False,
                                 cli_dry_run=None, force=False, daily_heartbeat=True)
    assert dry is False
    assert "heartbeat" in reason


def test_actionable_zero_plans_heartbeat_off_still_skips():
    dry, reason = decide_dry_run(actionable=True, n_plans=0, email_when_no_setups=False,
                                 cli_dry_run=None, force=False, daily_heartbeat=False)
    assert dry is True
    assert "no setups" in reason


def test_degraded_run_heartbeat_on_still_forces_dry():
    dry, reason = decide_dry_run(actionable=False, n_plans=0, email_when_no_setups=False,
                                 cli_dry_run=None, force=False, daily_heartbeat=True)
    assert dry is True
    assert "degraded" in reason


def test_degraded_run_heartbeat_on_force_overrides():
    dry, reason = decide_dry_run(actionable=False, n_plans=0, email_when_no_setups=False,
                                 cli_dry_run=None, force=True, daily_heartbeat=True)
    assert dry is not True


def test_actionable_with_plans_sends_regardless_of_heartbeat():
    dry, reason = decide_dry_run(actionable=True, n_plans=2, email_when_no_setups=False,
                                 cli_dry_run=None, force=False, daily_heartbeat=True)
    assert dry is None and reason == ""


def test_cli_dry_run_always_wins_over_heartbeat():
    dry, reason = decide_dry_run(actionable=True, n_plans=0, email_when_no_setups=False,
                                 cli_dry_run=True, force=False, daily_heartbeat=True)
    assert dry is True
    assert reason == "cli --dry-run"


def test_heartbeat_default_is_off_and_preserves_legacy_behavior():
    # daily_heartbeat not passed at all -> defaults False -> legacy skip-email path
    dry, reason = decide_dry_run(actionable=True, n_plans=0, email_when_no_setups=False,
                                 cli_dry_run=None, force=False)
    assert dry is True
    assert "no setups" in reason


# --------------------------------------------------------------------------- #
def test_render_heartbeat_marks_liveness_not_a_signal():
    from datetime import datetime, timezone

    from rmas.alerts.report import render_heartbeat
    from rmas.features.regime import Regime, RegimeState
    from rmas.pipeline.scan import ScanResult

    res = ScanResult(
        asof=datetime(2026, 7, 24, tzinfo=timezone.utc),
        candidates=[], plans=[],
        regime=RegimeState(regime=Regime.NEUTRAL, size_multiplier=1.0,
                           block_new_longs=False, reasons=[]),
        rejected={"discovery": 5, "tradeability": 2},
        reddit_mode="oauth", market_mode="live",
    )
    text, html = render_heartbeat(res, "meta-label WARMING UP (0/30 outcomes, both classes needed)",
                                  "forward-log: no records yet.")

    assert "heartbeat" in text.lower()
    assert "not a trade signal" in text.lower()
    assert "neutral" in text.lower()
    assert "discovery=5" in text
    assert "WARMING UP" in text
    assert "heartbeat" in html.lower()
    assert "not a trade signal" in html.lower()
