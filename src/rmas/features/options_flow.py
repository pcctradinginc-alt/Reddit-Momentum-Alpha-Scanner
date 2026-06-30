"""Options-flow confirmation features.

Options are a *confirmation* layer, never a standalone trigger. We look for
call/put imbalance, OI change, IV rank/percentile, skew and unusual call
activity — and we explicitly flag conditions that should BLOCK a trade
(extreme IV overpricing, blown-out spreads).
"""

from __future__ import annotations

from rmas.mathx import clip, squash
from rmas.types import OptionsSnapshot


def build_options_features(snap: OptionsSnapshot | None) -> dict[str, float]:
    if snap is None:
        return {
            "options_call_imbalance": 0.5,   # neutral when no data
            "_raw_iv_rank": 0.0,
            "_raw_iv_percentile": 0.0,
            "_raw_atm_spread_bps": 0.0,
            "_options_available": 0.0,
        }

    imb = snap.call_put_imbalance            # -1..1
    oi_chg = snap.oi_change_pct              # e.g. 0.25 = +25%
    unusual = 1.0 if snap.unusual_call_activity else 0.0

    # Combine into a single confirmation feature in [0,1].
    call_imbalance = clip(0.5 + 0.5 * imb)
    oi_boost = squash(oi_chg, scale=0.5)
    feature = clip(0.6 * call_imbalance + 0.25 * oi_boost + 0.15 * unusual)

    return {
        "options_call_imbalance": feature,
        "_raw_call_put_imbalance": imb,
        "_raw_oi_change_pct": oi_chg,
        "_raw_iv_rank": snap.iv_rank,
        "_raw_iv_percentile": snap.iv_percentile,
        "_raw_skew": snap.skew,
        "_raw_atm_spread_bps": snap.atm_spread_bps,
        "_raw_unusual_calls": unusual,
        "_options_available": 1.0,
    }


def iv_overpriced(iv_rank: float, iv_percentile: float, max_rank: float, max_pct: float) -> bool:
    """True if implied vol is so rich that long-premium edge is likely gone."""
    return iv_rank >= max_rank or iv_percentile >= max_pct


def iv_crush_risk(iv_rank: float, days_to_catalyst: float | None) -> bool:
    """High IV into a known near-term catalyst => crush risk for long options."""
    if days_to_catalyst is None:
        return False
    return iv_rank >= 70 and days_to_catalyst <= 3
