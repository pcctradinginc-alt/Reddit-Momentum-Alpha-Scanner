"""Market-regime filter.

Momentum strategies blow up in the wrong regime (risk-off, momentum crashes).
We classify the regime from VIX, index trend and breadth, and expose a size
multiplier and a hard "block new longs" flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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
