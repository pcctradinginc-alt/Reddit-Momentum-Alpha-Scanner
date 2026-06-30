"""Walk-forward evaluation — anchored, non-overlapping train/test windows.

We never optimize in-sample and report the same numbers. Instead we slide a
train window (to fit thresholds / meta-label model) followed by an out-of-sample
test window, and aggregate only the OOS results.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from rmas.backtest.engine import Signal, TradeResult
from rmas.backtest.metrics import Metrics, compute_metrics


@dataclass
class Window:
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


def make_windows(start: datetime, end: datetime, train_days: int, test_days: int) -> list[Window]:
    windows: list[Window] = []
    cursor = start
    step = timedelta(days=test_days)
    train = timedelta(days=train_days)
    while cursor + train + step <= end:
        tr_start = cursor
        tr_end = cursor + train
        te_start = tr_end
        te_end = tr_end + step
        windows.append(Window(tr_start, tr_end, te_start, te_end))
        cursor += step
    return windows


@dataclass
class WalkForwardReport:
    windows: int
    oos_trades: int
    oos_metrics: Metrics
    per_window: list[Metrics]


def walk_forward(
    signals: Sequence[Signal],
    run_fn: Callable[[Sequence[Signal]], list[TradeResult]],
    windows: Sequence[Window],
    fit_fn: Callable[[Sequence[Signal]], object] | None = None,
    apply_fn: Callable[[object, Sequence[Signal]], list[Signal]] | None = None,
) -> WalkForwardReport:
    """Run OOS evaluation across windows.

    ``fit_fn`` (optional) fits a model/thresholds on train signals; ``apply_fn``
    filters test signals using that fitted object (e.g. meta-label gate).
    """
    all_oos: list[TradeResult] = []
    per_window: list[Metrics] = []

    for w in windows:
        train_sigs = [s for s in signals if w.train_start <= s.t < w.train_end]
        test_sigs = [s for s in signals if w.test_start <= s.t < w.test_end]

        model = fit_fn(train_sigs) if fit_fn else None
        if model is not None and apply_fn:
            test_sigs = apply_fn(model, test_sigs)

        res = run_fn(test_sigs)
        all_oos.extend(res)
        per_window.append(compute_metrics([r.net_return for r in res]))

    return WalkForwardReport(
        windows=len(windows),
        oos_trades=len(all_oos),
        oos_metrics=compute_metrics([r.net_return for r in all_oos]),
        per_window=per_window,
    )
