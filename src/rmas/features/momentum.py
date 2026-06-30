"""Momentum / price-volume confirmation features.

No trade without price/volume confirmation. We compute relative strength,
breakouts, VWAP/EMA posture, relative volume, gaps and follow-through from
point-in-time OHLCV bars. Outputs squashed to [0,1] for weighting.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rmas.mathx import atr, clip, ema, pct_change, squash
from rmas.types import Bar


@dataclass
class MomentumInput:
    ticker: str
    bars: Sequence[Bar]                    # daily bars, oldest -> newest
    benchmark_returns: dict[str, float]    # {"SPY": 20d_return, "QQQ":..., "SECTOR":...}
    premarket_volume: float = 0.0          # today's premarket cumulative volume
    avg_premarket_volume: float = 0.0


def _returns(bars: Sequence[Bar], lookback: int) -> float:
    bars = list(bars)
    if len(bars) <= lookback:
        return 0.0
    return pct_change(bars[-1 - lookback].close, bars[-1].close)


def vwap(bars: Sequence[Bar], window: int = 20) -> float:
    bars = list(bars)[-window:]
    num = sum(((b.high + b.low + b.close) / 3.0) * b.volume for b in bars)
    den = sum(b.volume for b in bars) or 1.0
    return num / den


def build_momentum_features(inp: MomentumInput) -> dict[str, float]:
    bars = list(inp.bars)
    if len(bars) < 21:
        # not enough history to confirm momentum -> neutral-low features
        return {k: 0.0 for k in (
            "rel_strength_vs_spy", "rel_strength_vs_sector", "breakout_20d",
            "above_vwap", "above_ema20", "rel_volume", "premarket_volume_z",
        )}

    last = bars[-1]
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    vols = [b.volume for b in bars]

    r20 = _returns(bars, 20)
    spy = inp.benchmark_returns.get("SPY", 0.0)
    qqq = inp.benchmark_returns.get("QQQ", 0.0)
    sector = inp.benchmark_returns.get("SECTOR", spy)
    bench = max(spy, qqq)

    rs_spy = r20 - bench
    rs_sector = r20 - sector

    # breakout over prior 20-day high (exclude today to avoid lookahead).
    prior_high_20 = max(highs[-21:-1])
    breakout = pct_change(prior_high_20, last.close)  # >0 means new-high territory

    vw = vwap(bars, 20)
    ema20 = ema(closes, 20)[-1]
    above_vwap = (last.close - vw) / vw if vw else 0.0
    above_ema = (last.close - ema20) / ema20 if ema20 else 0.0

    avg_vol_20 = sum(vols[-21:-1]) / 20.0
    rel_vol = last.volume / avg_vol_20 if avg_vol_20 else 0.0

    pm_z = 0.0
    if inp.avg_premarket_volume > 0:
        pm_z = (inp.premarket_volume - inp.avg_premarket_volume) / inp.avg_premarket_volume

    feats = {
        "rel_strength_vs_spy": squash(rs_spy, scale=0.05),
        "rel_strength_vs_sector": squash(rs_sector, scale=0.05),
        "breakout_20d": squash(breakout, scale=0.03),
        "above_vwap": squash(above_vwap, scale=0.02),
        "above_ema20": squash(above_ema, scale=0.03),
        "rel_volume": clip((rel_vol - 1.0) / 3.0),     # 1x ->0, 4x ->1
        "premarket_volume_z": squash(pm_z, scale=1.0),
    }
    # raw values for the gate to inspect
    feats["_raw_rel_strength"] = rs_spy
    feats["_raw_breakout_pct"] = breakout
    feats["_raw_rel_volume"] = rel_vol
    feats["_raw_atr"] = atr(highs, lows, closes, 14)
    feats["_raw_close"] = last.close
    feats["_raw_r5"] = _returns(bars, 5)
    feats["_raw_gap_pct"] = pct_change(bars[-2].close, last.open) if len(bars) >= 2 else 0.0
    return feats


def is_parabolic(r5_pct: float, threshold_pct: float = 60.0) -> bool:
    """Blow-off detector: >threshold% in 5 sessions is parabolic."""
    return (r5_pct * 100.0) >= threshold_pct
