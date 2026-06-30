"""Strategy C — Post-Earnings Reddit Drift.

Recent earnings/guidance + price strength + *nascent* reddit attention. The
edge is the drift after a confirmed fundamental catalyst, with reddit arriving
early rather than at the blow-off.
"""

from __future__ import annotations

from rmas.strategies.base import StrategyContext, register
from rmas.types import Candidate


@register
class PostEarningsDrift:
    name = "C_post_earnings_drift"
    direction = "long"
    instrument = "equity"

    max_days_since_earnings = 5

    def qualifies(self, cand: Candidate, ctx: StrategyContext) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        blockers: list[str] = []
        if not cand.all_green:
            blockers.append("gates_not_all_green")

        dse = ctx.days_since_earnings
        if dse is None or dse > self.max_days_since_earnings:
            blockers.append("no_recent_earnings")
        else:
            reasons.append(f"days_since_earnings={dse}")

        # price strength already encoded in tradeability green; require positive RS.
        rs = cand.features.get("_raw_rel_strength", 0.0)
        if rs <= 0:
            blockers.append("no_price_strength")
        else:
            reasons.append(f"rel_strength={rs:.3f}")

        return (len(blockers) == 0), (reasons if not blockers else blockers)
