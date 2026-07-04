"""Market-regime filter.

Momentum strategies blow up in the wrong regime (risk-off, momentum crashes).
We classify the regime from VIX, index trend and breadth, and expose a size
multiplier and a hard "block new longs" flag.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from rmas.mathx import mean, pct_change, stdev
from rmas.types import Bar


class Regime(str, Enum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"
    MOMENTUM_CRASH = "momentum_crash"


@dataclass
class RegimeInput:
    vix: float
    index_above_200dma: bool
    index_20d_return: float          # SPY 20d return
    breadth_pct_above_50dma: float   # 0..100, % of market above 50dma
    momentum_factor_5d_return: float = 0.0  # crowded-momentum proxy


@dataclass
class RegimeState:
    regime: Regime
    size_multiplier: float           # scales position size
    block_new_longs: bool
    reasons: list[str]


def regime_input_from_bars(spy_bars: Sequence[Bar],
                           qqq_bars: Sequence[Bar] | None = None) -> RegimeInput:
    """Build a real RegimeInput from index bars — no extra API or data cost.

    VIX proxy: 20d realized SPY volatility, annualized (tracks VIX closely
    enough for a risk-on/off switch). Breadth proxy: % of the last 50 sessions
    SPY closed above its 50dma (true breadth needs a whole-market feed).
    Momentum-crash proxy: QQQ-minus-SPY 5d return spread (crowded-growth unwind).
    """
    closes = [b.close for b in spy_bars]
    if len(closes) < 21:
        return RegimeInput(vix=20.0, index_above_200dma=True, index_20d_return=0.0,
                           breadth_pct_above_50dma=55.0)

    dma200 = mean(closes[-200:]) if len(closes) >= 200 else mean(closes)
    r20 = pct_change(closes[-21], closes[-1])

    rets = [pct_change(closes[i - 1], closes[i]) for i in range(len(closes) - 20, len(closes))]
    vix_proxy = stdev(rets) * math.sqrt(252) * 100.0

    above_50dma_days = 0
    window = min(50, len(closes) - 1)
    for i in range(len(closes) - window, len(closes)):
        d50 = mean(closes[max(0, i - 49):i + 1])
        if closes[i] > d50:
            above_50dma_days += 1
    breadth = above_50dma_days / window * 100.0 if window else 55.0

    mom_5d = 0.0
    if qqq_bars and len(qqq_bars) >= 6 and len(closes) >= 6:
        q = [b.close for b in qqq_bars]
        mom_5d = pct_change(q[-6], q[-1]) - pct_change(closes[-6], closes[-1])

    return RegimeInput(
        vix=round(vix_proxy, 2),
        index_above_200dma=closes[-1] > dma200,
        index_20d_return=round(r20, 4),
        breadth_pct_above_50dma=round(breadth, 1),
        momentum_factor_5d_return=round(mom_5d, 4),
    )


def classify_regime(inp: RegimeInput, vix_risk_off: float = 28, vix_calm: float = 16) -> RegimeState:
    reasons: list[str] = []

    momentum_crash = inp.momentum_factor_5d_return <= -0.08 and inp.vix >= vix_risk_off
    if momentum_crash:
        reasons.append("momentum_factor_unwind")
        return RegimeState(Regime.MOMENTUM_CRASH, 0.0, True, reasons)

    if inp.vix >= vix_risk_off or not inp.index_above_200dma:
        if inp.vix >= vix_risk_off:
            reasons.append(f"vix>={vix_risk_off}")
        if not inp.index_above_200dma:
            reasons.append("index_below_200dma")
        return RegimeState(Regime.RISK_OFF, 0.4, inp.vix >= vix_risk_off, reasons)

    if inp.vix <= vix_calm and inp.index_above_200dma and inp.breadth_pct_above_50dma >= 55:
        reasons.append("calm_vix_broad_breadth")
        return RegimeState(Regime.RISK_ON, 1.0, False, reasons)

    reasons.append("mixed")
    return RegimeState(Regime.NEUTRAL, 0.75, False, reasons)
