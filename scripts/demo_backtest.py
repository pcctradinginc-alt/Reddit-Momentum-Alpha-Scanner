"""Demo walk-forward backtest on synthetic data.

Generates signals across a date range, runs the point-in-time engine with the
realistic cost model, fits a meta-label model on each train window and applies
it OOS, then prints EV/PF/Sortino/MaxDD overall and per regime.

This is a *mechanics* demo (synthetic data) — wire real point-in-time data via
the adapters for genuine research. Run:  python -m scripts.demo_backtest
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from rmas.backtest.costs import CostConfig
from rmas.backtest.engine import Backtester, ExitPolicy, Signal
from rmas.backtest.metrics import compute_metrics, metrics_by_regime
from rmas.backtest.meta_labeling import MetaLabeler
from rmas.backtest.walkforward import make_windows, walk_forward
from rmas.data.base import SyntheticMarket
from rmas.mathx import atr

REGIMES = ["ai_bull", "meme_phase", "bear", "momentum_rout"]
TICKERS = ["NVDA", "AMD", "PLTR", "SOFI", "GME", "MARA", "TSLA", "AI", "RIOT", "COIN"]


def _build_signals(n_days: int = 360, seed: int = 7) -> tuple[list[Signal], dict[str, list]]:
    rng = random.Random(seed)
    market = SyntheticMarket()
    bars_by_ticker = {t: market.daily_bars(t, n_days + 30) for t in TICKERS}

    signals: list[Signal] = []
    start = datetime.now(timezone.utc) - timedelta(days=n_days)
    for d in range(0, n_days, 2):
        day = start + timedelta(days=d)
        regime = REGIMES[(d // 45) % len(REGIMES)]
        for t in TICKERS:
            if rng.random() > 0.25:
                continue
            bars = bars_by_ticker[t]
            # find bar index near 'day'
            idx = min(range(len(bars)), key=lambda i: abs((bars[i].t - day).days))
            window = bars[max(0, idx - 14): idx + 1]
            a = atr([b.high for b in window], [b.low for b in window],
                    [b.close for b in window], 14)
            # crude "signal feature" used by meta-labeler
            mom = (window[-1].close - window[0].close) / window[0].close if window else 0.0
            signals.append(Signal(
                ticker=t, t=bars[idx].t, direction="long",
                strategy="A_early_momentum_long", regime=regime, atr=a,
                meta={"mom": mom, "atr_pct": a / max(0.01, bars[idx].close)},
            ))
    return signals, bars_by_ticker


def main() -> int:
    signals, bars_by_ticker = _build_signals()
    cost = CostConfig()
    policy = ExitPolicy()
    bt = Backtester(cost, policy)

    def run_fn(sigs):
        return bt.run(sigs, bars_by_ticker)

    # ---- baseline (no meta-label) ----
    base_results = run_fn(signals)
    base_metrics = compute_metrics([r.net_return for r in base_results])

    # ---- meta-labeling fit/apply per walk-forward window ----
    feat_names = ["mom", "atr_pct"]

    def fit_fn(train_sigs):
        res = bt.run(train_sigs, bars_by_ticker)
        by_id = {(r.ticker, r.entry_t): r for r in res}
        X, y = [], []
        for s in train_sigs:
            # label = did this signal's trade make money after costs?
            match = next((r for r in res if r.ticker == s.ticker), None)
            if match is None:
                continue
            X.append([s.meta.get(f, 0.0) for f in feat_names])
            y.append(1 if match.net_return > 0 else 0)
        return MetaLabeler(feat_names).fit(X, y) if X else None

    def apply_fn(model, test_sigs):
        kept = []
        for s in test_sigs:
            p = model.predict_proba({f: s.meta.get(f, 0.0) for f in feat_names})
            if p >= 0.5:
                kept.append(s)
        return kept

    start = min(s.t for s in signals)
    end = max(s.t for s in signals)
    windows = make_windows(start, end, train_days=120, test_days=45)
    wf = walk_forward(signals, run_fn, windows, fit_fn=fit_fn, apply_fn=apply_fn)

    by_regime = metrics_by_regime([(r.regime, r.net_return) for r in base_results])

    # ---- report ----
    print("=" * 70)
    print("RMAS DEMO BACKTEST (synthetic data — mechanics only)")
    print("=" * 70)
    print(f"\nBaseline (all signals): {base_metrics.n_trades} trades")
    _print_metrics(base_metrics)

    print(f"\nWalk-forward OOS (meta-labeled): {wf.oos_trades} trades over {wf.windows} windows")
    _print_metrics(wf.oos_metrics)

    print("\nPer-regime (baseline):")
    for regime, m in by_regime.items():
        print(f"  [{regime:>14}] n={m.n_trades:4d}  EV={m.expected_value:+.4f}  "
              f"PF={m.profit_factor:.2f}  Sortino={m.sortino:.2f}  MaxDD={m.max_drawdown:.2%}")
    print("\nNote: optimize EV-after-cost & stability across regimes, not hit-rate.")
    return 0


def _print_metrics(m) -> None:
    print(f"  EV/trade (after cost): {m.expected_value:+.4f}")
    print(f"  Profit Factor        : {m.profit_factor:.3f}")
    print(f"  Win Rate             : {m.win_rate:.1%}  (avg win {m.avg_win:+.4f} / avg loss {m.avg_loss:+.4f})")
    print(f"  Payoff ratio         : {m.payoff_ratio:.2f}")
    print(f"  Sortino / Sharpe     : {m.sortino:.2f} / {m.sharpe:.2f}")
    print(f"  Max Drawdown         : {m.max_drawdown:.2%}")
    print(f"  Total Return (compounded): {m.total_return:+.2%}")


if __name__ == "__main__":
    raise SystemExit(main())
