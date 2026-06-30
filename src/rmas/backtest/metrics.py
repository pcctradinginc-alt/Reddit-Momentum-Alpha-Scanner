"""Performance metrics — optimized target is EV-after-cost, NOT hit-rate.

All functions operate on a list of per-trade net returns (fractional, after
costs). Equity-curve metrics (Sortino, MaxDD) operate on the cumulative curve.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from rmas.mathx import mean, stdev


@dataclass
class Metrics:
    n_trades: int
    expected_value: float        # mean net return per trade (THE target)
    profit_factor: float
    win_rate: float
    avg_win: float
    avg_loss: float
    payoff_ratio: float          # avg_win / |avg_loss|
    sortino: float
    sharpe: float
    max_drawdown: float
    total_return: float
    cagr_proxy: float

    def as_dict(self) -> dict:
        return asdict(self)


def profit_factor(returns: Sequence[float]) -> float:
    gains = sum(r for r in returns if r > 0)
    losses = -sum(r for r in returns if r < 0)
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def sortino_ratio(returns: Sequence[float], target: float = 0.0) -> float:
    rs = list(returns)
    if len(rs) < 2:
        return 0.0
    excess = [r - target for r in rs]
    downside = [min(0.0, e) for e in excess]
    dd = math.sqrt(mean([d * d for d in downside]))
    if dd == 0:
        return float("inf") if mean(excess) > 0 else 0.0
    return mean(excess) / dd


def sharpe_ratio(returns: Sequence[float]) -> float:
    rs = list(returns)
    s = stdev(rs)
    return mean(rs) / s if s > 0 else 0.0


def equity_curve(returns: Sequence[float], start: float = 1.0, compounding: bool = True) -> list[float]:
    curve = [start]
    for r in returns:
        if compounding:
            curve.append(curve[-1] * (1.0 + r))
        else:
            curve.append(curve[-1] + r)
    return curve


def max_drawdown(curve: Sequence[float]) -> float:
    """Max peak-to-trough drawdown as a positive fraction (0.2 = -20%)."""
    peak = -float("inf")
    mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd


def compute_metrics(returns: Sequence[float], periods_per_year: int = 50) -> Metrics:
    rs = list(returns)
    n = len(rs)
    if n == 0:
        return Metrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    avg_win = mean(wins) if wins else 0.0
    avg_loss = mean(losses) if losses else 0.0
    curve = equity_curve(rs)

    ev = mean(rs)
    sharpe = sharpe_ratio(rs)
    # Annualize-ish using assumed trade cadence (proxy only).
    cagr = (curve[-1]) ** (periods_per_year / n) - 1.0 if curve[-1] > 0 and n > 0 else -1.0

    return Metrics(
        n_trades=n,
        expected_value=round(ev, 6),
        profit_factor=round(profit_factor(rs), 4),
        win_rate=round(len(wins) / n, 4),
        avg_win=round(avg_win, 6),
        avg_loss=round(avg_loss, 6),
        payoff_ratio=round(avg_win / abs(avg_loss), 4) if avg_loss != 0 else float("inf"),
        sortino=round(sortino_ratio(rs), 4),
        sharpe=round(sharpe, 4),
        max_drawdown=round(max_drawdown(curve), 4),
        total_return=round(curve[-1] - 1.0, 4),
        cagr_proxy=round(cagr, 4),
    )


def metrics_by_regime(trades: Sequence[tuple[str, float]]) -> dict[str, Metrics]:
    """``trades`` = list of (regime_label, net_return). Group + compute per regime."""
    buckets: dict[str, list[float]] = {}
    for regime, ret in trades:
        buckets.setdefault(regime, []).append(ret)
    return {k: compute_metrics(v) for k, v in buckets.items()}
