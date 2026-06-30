"""Command-line entrypoint.

    rmas scan            run the daily scan, print ranked setups
    rmas alert           run scan + render report + (optionally) email it
    rmas paper           run scan + open paper positions for the top setups
    rmas backtest        run the demo walk-forward backtest on synthetic data
    rmas selfcheck       verify the install runs end-to-end offline

All commands run offline by default (RMAS_OFFLINE=true) with synthetic data.
"""

from __future__ import annotations

import argparse
import sys

from rmas.config import Secrets, load_config
from rmas.logging_setup import setup_logging


def _cmd_scan(args) -> int:
    from rmas.pipeline.scan import run_scan

    cfg = load_config()
    res = run_scan(cfg=cfg, offline=args.offline)
    print(res.summary())
    for p in res.plans:
        print(f"  • {p.ticker} [{p.strategy}] entry={p.entry} stop={p.stop} "
              f"targets={p.targets} size={p.shares} edge={p.expected_edge_bps}bps")
    if not res.plans:
        print("  (no qualifying setups — staying flat)")
    return 0


def _cmd_alert(args) -> int:
    from rmas.alerts.email_smtp import send_email
    from rmas.alerts.report import render_html, render_text
    from rmas.pipeline.scan import run_scan

    cfg = load_config()
    res = run_scan(cfg=cfg, offline=args.offline)
    text = render_text(res.plans, res.asof)
    html = render_html(res.plans, res.asof)
    print(text)
    subject = f"RMAS — {len(res.plans)} setup(s) {res.asof:%Y-%m-%d}"
    sent = send_email(subject, text, html, secrets=Secrets(), dry_run=args.dry_run)
    print(f"[email {'SENT' if sent else 'dry-run / saved to reports/'}]")
    return 0


def _cmd_paper(args) -> int:
    from rmas.paper.broker import Blotter
    from rmas.pipeline.scan import run_scan

    cfg = load_config()
    res = run_scan(cfg=cfg, offline=args.offline)
    blotter = Blotter.load()
    for p in res.plans:
        blotter.open_from_plan(p)
    blotter.save()
    print(f"Opened {len(res.plans)} paper position(s). "
          f"Open total: {len(blotter.open_positions)}. Realized P&L: {blotter.realized_pnl}")
    return 0


def _cmd_backtest(args) -> int:
    from scripts.demo_backtest import main as demo_main  # type: ignore

    return demo_main()


def _cmd_selfcheck(args) -> int:
    from rmas.pipeline.scan import run_scan

    res = run_scan(offline=True)
    ok = res is not None
    print("SELFCHECK:", "OK" if ok else "FAIL", "-", res.summary())
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rmas", description="Reddit + Momentum Alpha Scanner")
    p.add_argument("--offline", dest="offline", action="store_true", default=None,
                   help="force offline/synthetic adapters")
    p.add_argument("--live", dest="offline", action="store_false",
                   help="use live adapters (needs keys in .env)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan", help="run the daily scan").set_defaults(func=_cmd_scan)
    a = sub.add_parser("alert", help="scan + report + email")
    a.add_argument("--dry-run", action="store_true", default=None)
    a.set_defaults(func=_cmd_alert)
    sub.add_parser("paper", help="scan + open paper positions").set_defaults(func=_cmd_paper)
    sub.add_parser("backtest", help="demo walk-forward backtest").set_defaults(func=_cmd_backtest)
    sub.add_parser("selfcheck", help="end-to-end offline smoke test").set_defaults(func=_cmd_selfcheck)
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
