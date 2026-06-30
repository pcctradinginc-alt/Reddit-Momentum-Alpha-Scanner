"""Volatility- and risk-based position sizing.

Risk-per-trade is a fixed fraction of equity; the stop distance (ATR-based)
determines share count. Size is then scaled by the regime multiplier. This
keeps dollar risk roughly constant regardless of the name's volatility.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SizingResult:
    shares: int
    risk_usd: float
    stop: float
    entry: float
    notional_usd: float
    r_per_share: float


def atr_stop(entry: float, atr_value: float, mult: float, direction: str = "long") -> float:
    dist = atr_value * mult
    return entry - dist if direction == "long" else entry + dist


def size_position(
    entry: float,
    atr_value: float,
    *,
    account_equity: float,
    risk_per_trade_pct: float,
    atr_stop_mult: float,
    direction: str = "long",
    regime_multiplier: float = 1.0,
    max_notional_pct: float = 25.0,
) -> SizingResult:
    """Return integer share count sized to risk ``risk_per_trade_pct`` of equity.

    Guards: positive ATR & entry, regime scaling, and a notional cap so a
    tight stop can't create an oversized position.
    """
    risk_budget = account_equity * (risk_per_trade_pct / 100.0) * max(0.0, regime_multiplier)
    stop = atr_stop(entry, atr_value, atr_stop_mult, direction)
    r_per_share = abs(entry - stop)

    if r_per_share <= 0 or entry <= 0 or risk_budget <= 0:
        return SizingResult(0, 0.0, stop, entry, 0.0, r_per_share)

    shares = int(risk_budget // r_per_share)

    # Notional cap.
    max_notional = account_equity * (max_notional_pct / 100.0)
    if shares * entry > max_notional:
        shares = int(max_notional // entry)

    notional = shares * entry
    risk_usd = shares * r_per_share
    return SizingResult(shares, round(risk_usd, 2), round(stop, 4), entry,
                        round(notional, 2), round(r_per_share, 4))
