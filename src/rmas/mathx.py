"""Small, dependency-free numeric helpers used across features & metrics.

Kept stdlib-only (no numpy) so the engine and its tests run anywhere.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def mean(xs: Sequence[float]) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs: Sequence[float], ddof: int = 1) -> float:
    xs = list(xs)
    n = len(xs)
    if n - ddof <= 0:
        return 0.0
    m = mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (n - ddof)
    return math.sqrt(max(0.0, var))


def zscore(x: float, history: Sequence[float], floor_std: float = 1e-9) -> float:
    """z-score of ``x`` vs the distribution in ``history``."""
    history = list(history)
    if len(history) < 2:
        return 0.0
    m = mean(history)
    s = stdev(history)
    return (x - m) / max(s, floor_std)


def clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def squash(x: float, scale: float = 1.0) -> float:
    """Map any real to [0,1] via a logistic with the given scale (x/scale)."""
    return sigmoid(x / scale) if scale else sigmoid(x)


def ema(values: Sequence[float], span: int) -> list[float]:
    """Exponential moving average; returns a series the same length as input."""
    values = list(values)
    if not values:
        return []
    alpha = 2.0 / (span + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def pct_change(a: float, b: float) -> float:
    """(b - a) / a, guarded against zero base."""
    return (b - a) / a if a not in (0, 0.0) else 0.0


def rolling_max(values: Sequence[float], window: int) -> float:
    vals = list(values)[-window:]
    return max(vals) if vals else float("nan")


def true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> float:
    """Average true range over the last ``period`` bars."""
    n = len(closes)
    if n < 2:
        return 0.0
    trs = [
        true_range(highs[i], lows[i], closes[i - 1])
        for i in range(1, n)
    ]
    trs = trs[-period:]
    return mean(trs)


def weighted_score(features: dict[str, float], weights: dict[str, float]) -> float:
    """Weighted average over the shared keys; missing features count as 0.

    Returns a value in the same units as the (already-normalized) features —
    callers pass features pre-squashed to [0,1] so the result is in [0,1].
    """
    total_w = sum(weights.values()) or 1.0
    acc = 0.0
    for key, w in weights.items():
        acc += w * float(features.get(key, 0.0))
    return acc / total_w
