"""Strategy A — Early Reddit Momentum Long (stock/ETF, 1-10 day hold)."""

from __future__ import annotations

from rmas.strategies.base import StrategyContext, register
from rmas.types import Candidate


@register
class EarlyMomentumLong:
    name = "A_early_momentum_long"
    direction = "long"
    instrument = "equity"

    def qualifies(self, cand: Candidate, ctx: StrategyContext) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if not cand.all_green:
            return False, ["gates_not_all_green"]
        # Avoid already-parabolic names (that's strategy D's territory).
        if ctx.parabolic_5d_pct >= 60:
            return False, ["parabolic_move"]
        reasons.append("all_gates_green")
        reasons.append("not_parabolic")
        return True, reasons
