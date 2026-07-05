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


def decide_dry_run(actionable: bool, n_plans: int, email_when_no_setups: bool,
                   cli_dry_run: bool | None, force: bool) -> tuple[bool | None, str]:
    """Email policy: (dry_run, reason).

    - Degraded (synthetic Reddit) runs never email unless --force.
    - Empty runs (0 setups) only email when configured — saves inbox noise;
      the report is always written to reports/ either way.
    """
    if cli_dry_run:
        return True, "cli --dry-run"
    if not actionable and not force:
        return True, "degraded run (reddit synthetic) — forcing dry-run; use --force to email anyway"
    if actionable and n_plans == 0 and not email_when_no_setups and not force:
        return True, "no setups today — skipping email (run.email_when_no_setups=false)"
    return cli_dry_run, ""


def _cmd_alert(args) -> int:
    from rmas.alerts.email_smtp import send_email
    from rmas.alerts.report import render_html, render_text
    from rmas.pipeline.scan import run_scan

    cfg = load_config()
    res = run_scan(cfg=cfg, offline=args.offline)
    # Observability: the rejected-per-gate breakdown is what makes threshold
    # tuning from CI logs possible — always print it.
    print(res.summary())
    text = render_text(res.plans, res.asof, actionable=res.actionable)
    html = render_html(res.plans, res.asof, actionable=res.actionable)
    print(text)

    dry_run, reason = decide_dry_run(
        res.actionable, len(res.plans),
        bool(cfg.run.get("email_when_no_setups", False)),
        args.dry_run, args.force,
    )
    if reason:
        print(f"[guard] {reason}")

    tag = "TEST" if not res.actionable else f"{len(res.plans)} setup(s)"
    subject = f"RMAS {tag} — {res.asof:%Y-%m-%d}"
    sent = send_email(subject, text, html, secrets=Secrets(), dry_run=dry_run)
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


def _cmd_doctor(args) -> int:
    """Live data diagnostics: probe every adapter and report LIVE vs FALLBACK.

    The daily scan only touches per-ticker APIs for discovery-green names, so
    a cold-start scan exercises almost nothing — this command forces every
    data path with one sample ticker and prints what is real."""
    from rmas.data.apewisdom_source import ApeWisdomAdapter
    from rmas.data.fundamentals_finnhub import FinnhubFundamentals
    from rmas.data.market_alpaca import AlpacaAdapter
    from rmas.data.news_source import NewsAdapter
    from rmas.data.options_tradier import TradierAdapter
    from rmas.data.reddit_source import RedditAdapter
    from rmas.data.trends_source import TrendsAdapter
    from rmas.features.sector_map import sector_etf

    cfg = load_config()
    secrets = Secrets()
    off = bool(args.offline) if args.offline is not None else False
    t = args.ticker.upper()
    rows: list[tuple[str, str, str]] = []

    def row(name: str, live: bool, detail: str) -> None:
        rows.append((name, "LIVE" if live else "FALLBACK", detail))

    market = AlpacaAdapter(secrets, offline=off)
    bars = market.daily_bars(t, 60)
    row("alpaca bars", market.feed_used is not None,
        f"n={len(bars)} feed={market.feed_used or 'synthetic'} "
        f"close={bars[-1].close if bars else '?'} vol={int(bars[-1].volume) if bars else '?'}")
    spread = market.spread_bps(t)
    row("alpaca quote spread", spread is not None,
        f"{round(spread, 1)} bps" if spread is not None else "n/a")
    bench = market.benchmark_returns(20)
    row("benchmarks 20d", market.feed_used is not None,
        f"SPY={bench.get('SPY', 0):+.3f} QQQ={bench.get('QQQ', 0):+.3f}")
    pm = market.premarket_volumes(t)
    row("premarket volume", pm is not None,
        f"today={int(pm[0])} avg5d={int(pm[1])}" if pm else "n/a (closed/weekend or no data)")

    funda = FinnhubFundamentals(secrets, offline=off)
    cap = funda.market_cap_usd(t)
    ind = funda.industry(t)
    shares = funda.shares_outstanding(t)
    advol = funda.avg_dollar_volume_usd(t, bars[-1].close if bars else 0.0)
    row("finnhub profile", cap is not None,
        f"cap={cap / 1e9:.1f}B sharesOut={int((shares or 0) / 1e6)}M industry={ind} "
        f"sector_etf={sector_etf(ind)}" if cap else "n/a")
    row("finnhub 10d $-volume", advol is not None,
        f"{advol / 1e6:.0f}M$/day" if advol else "n/a")

    options = TradierAdapter(secrets, offline=off)
    snap = options.options_snapshot(t)
    row("tradier options", getattr(options, "_live", False),
        f"call_vol={int(snap.call_volume)} put_vol={int(snap.put_volume)} "
        f"iv_rank={snap.iv_rank:.0f}")

    news = NewsAdapter(secrets, offline=off)
    hl = news.headlines(t)
    row("news headlines", not off and (secrets.has("FINNHUB_API_KEY") or secrets.alpaca_ready),
        f"n={len(hl)}")

    hype = ApeWisdomAdapter(secrets, offline=off)
    top = hype.top()
    row("apewisdom hype list", bool(top),
        f"n={len(top)} rank({t})={hype.rank(t)}")

    trends = TrendsAdapter(secrets, offline=off)
    row("google trends", not off, f"z={trends.google_trends_z(t)}")
    row("x attention", False, f"z={trends.x_attention_z(t)} (no impl; neutral live)")

    reddit = RedditAdapter(secrets, offline=off)
    subs = list(cfg.universe.subreddits)[:2]
    ms = reddit.fetch_mentions(subs, 24, limit_per_sub=30)
    row("reddit radar", reddit.is_live,
        f"mode={reddit.mode} coverage={reddit.coverage:.0%} posts={len(ms)} subs={subs}")

    print(f"\nRMAS DOCTOR — sample ticker {t}\n" + "=" * 64)
    for name, status, detail in rows:
        print(f"  {name:<22} {status:<9} {detail}")
    n_live = sum(1 for _, s, _ in rows if s == "LIVE")
    print("=" * 64 + f"\n  {n_live}/{len(rows)} data paths LIVE\n")
    return 0


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
    a.add_argument("--force", action="store_true", default=False,
                   help="email even on a degraded (synthetic-reddit) run")
    a.set_defaults(func=_cmd_alert)
    sub.add_parser("paper", help="scan + open paper positions").set_defaults(func=_cmd_paper)
    d = sub.add_parser("doctor", help="probe every live data path with a sample ticker")
    d.add_argument("--ticker", default="NVDA")
    d.set_defaults(func=_cmd_doctor)
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
