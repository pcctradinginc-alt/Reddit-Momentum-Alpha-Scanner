"""Email policy: no fake signals, no empty-inbox noise, no accidental sends."""

from rmas.cli import decide_dry_run


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
