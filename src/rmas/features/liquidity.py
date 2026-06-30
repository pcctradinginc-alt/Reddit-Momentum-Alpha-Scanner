"""Liquidity / tradability hard-filters.

Even a perfect signal is untradeable if you can't get in/out at sane cost.
These return both a quality score and hard pass/fail booleans the gate uses.
"""

from __future__ import annotations

from rmas.mathx import clip
from rmas.types import LiquiditySnapshot


def build_liquidity_features(liq: LiquiditySnapshot | None) -> dict[str, float]:
    if liq is None:
        return {"liquidity_quality": 0.0, "_raw_spread_bps": 9999.0, "_liq_available": 0.0}

    mc = clip((liq.market_cap_usd) / 2_000_000_000)          # $2B -> 1.0
    dv = clip((liq.dollar_volume_usd) / 50_000_000)          # $50M/day -> 1.0
    spread = clip(1.0 - liq.bid_ask_spread_bps / 100.0)      # 0bps ->1, 100bps ->0
    quality = clip(0.4 * mc + 0.35 * dv + 0.25 * spread)

    return {
        "liquidity_quality": quality,
        "_raw_market_cap": liq.market_cap_usd,
        "_raw_dollar_volume": liq.dollar_volume_usd,
        "_raw_spread_bps": liq.bid_ask_spread_bps,
        "_raw_short_interest_pct": liq.short_interest_pct,
        "_raw_float_shares": liq.float_shares,
        "_raw_borrow_available": 1.0 if liq.borrow_available else 0.0,
        "_liq_available": 1.0,
    }


def passes_liquidity(
    liq: LiquiditySnapshot | None,
    min_market_cap: float,
    min_dollar_volume: float,
    max_spread_bps: float,
) -> tuple[bool, list[str]]:
    """Hard liquidity gate. Returns (ok, list_of_blockers)."""
    blockers: list[str] = []
    if liq is None:
        return False, ["no_liquidity_data"]
    if liq.market_cap_usd < min_market_cap:
        blockers.append(f"market_cap<{min_market_cap:,.0f}")
    if liq.dollar_volume_usd < min_dollar_volume:
        blockers.append(f"dollar_volume<{min_dollar_volume:,.0f}")
    if liq.bid_ask_spread_bps > max_spread_bps:
        blockers.append(f"spread>{max_spread_bps:.0f}bps")
    return (len(blockers) == 0), blockers
