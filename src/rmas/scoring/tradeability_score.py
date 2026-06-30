"""Tradeability gate: price/volume/options confirmation + liquidity hard-filters."""

from __future__ import annotations

from rmas.features.liquidity import passes_liquidity
from rmas.features.options_flow import iv_overpriced
from rmas.mathx import weighted_score
from rmas.scoring.gates import make_gate
from rmas.types import GateResult, LiquiditySnapshot


def score_tradeability(features: dict[str, float], cfg, liq: LiquiditySnapshot | None = None,
                       min_market_cap: float = 150_000_000,
                       min_dollar_volume: float = 5_000_000) -> GateResult:
    weights = cfg.weights.to_dict() if hasattr(cfg.weights, "to_dict") else dict(cfg.weights)
    gate = cfg.gate

    score = weighted_score(features, weights)
    reasons: list[str] = []
    blockers: list[str] = []

    # Require positive relative strength.
    if gate.get("require_rel_strength_positive", True):
        rs = features.get("_raw_rel_strength", 0.0)
        if rs <= 0:
            blockers.append(f"rel_strength<=0 ({rs:.3f})")
        else:
            reasons.append(f"rel_strength={rs:.3f}")

    # Relative volume floor.
    rv = features.get("_raw_rel_volume", 0.0)
    min_rv = gate.get("min_rel_volume", 1.5)
    if rv < min_rv:
        blockers.append(f"rel_volume={rv:.2f}<{min_rv}")
    else:
        reasons.append(f"rel_volume={rv:.2f}")

    # Liquidity hard gate.
    ok_liq, liq_blockers = passes_liquidity(
        liq, min_market_cap, min_dollar_volume, gate.get("max_bid_ask_spread_bps", 60)
    )
    if not ok_liq:
        blockers.extend(liq_blockers)

    # IV overpricing block (only if options data present).
    if features.get("_options_available", 0.0) >= 1.0:
        if iv_overpriced(
            features.get("_raw_iv_rank", 0.0),
            features.get("_raw_iv_percentile", 0.0),
            gate.get("max_iv_rank", 92),
            gate.get("max_iv_percentile", 95),
        ):
            blockers.append("iv_overpriced")
        else:
            reasons.append(f"iv_rank={features.get('_raw_iv_rank',0):.0f}_ok")

    return make_gate("tradeability", score, gate.get("min_score", 0.55), reasons, blockers)
