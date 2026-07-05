"""Email policy: no fake signals, no empty-inbox noise, no accidental sends."""

from datetime import datetime, timezone

from rmas.cli import decide_dry_run
from rmas.pipeline.scan import ScanResult


def _res(reddit_mode: str, market_mode: str) -> ScanResult:
    return ScanResult(asof=datetime(2026, 7, 1, tzinfo=timezone.utc),
                      reddit_mode=reddit_mode, market_mode=market_mode)


def test_actionable_requires_reddit_AND_market_live():
    assert _res("oauth", "live").actionable
    assert _res("public_json", "live").actionable
    assert _res("rss", "live").actionable
    assert not _res("rss", "synthetic").actionable
    # live Reddit but synthetic market data = fake-confirmed signals -> blocked
    assert not _res("oauth", "synthetic").actionable
    assert not _res("public_json", "synthetic").actionable
    assert not _res("synthetic", "live").actionable
    assert not _res("synthetic", "synthetic").actionable


def test_degraded_run_forces_dry_run():
    dry, reason = decide_dry_run(actionable=False, n_plans=2, email_when_no_setups=False,
                                 cli_dry_run=None, force=False)
    assert dry is True
    assert "degraded" in reason


def test_degraded_run_force_overrides():
    dry, _ = decide_dry_run(actionable=False, n_plans=2, email_when_no_setups=False,
                            cli_dry_run=None, force=True)
    assert dry is not True


def test_no_setups_skips_email_by_default():
    dry, reason = decide_dry_run(actionable=True, n_plans=0, email_when_no_setups=False,
                                 cli_dry_run=None, force=False)
    assert dry is True
    assert "no setups" in reason


def test_no_setups_emails_when_configured():
    dry, reason = decide_dry_run(actionable=True, n_plans=0, email_when_no_setups=True,
                                 cli_dry_run=None, force=False)
    assert dry is None and reason == ""


def test_actionable_setups_send():
    dry, reason = decide_dry_run(actionable=True, n_plans=2, email_when_no_setups=False,
                                 cli_dry_run=None, force=False)
    assert dry is None and reason == ""


def test_cli_dry_run_always_wins():
    dry, _ = decide_dry_run(actionable=True, n_plans=3, email_when_no_setups=True,
                            cli_dry_run=True, force=True)
    assert dry is True
