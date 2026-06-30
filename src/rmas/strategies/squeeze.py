"""Strategy B — Reddit Squeeze Watch.

High short interest + small/mid float + call flow + breakout. Prefers equity or
a defined-risk call spread (instrument label informs sizing downstream).
"""

from __future__ import annotations

from rmas.strategies.base import StrategyContext, register
from rmas.types import Candidate


@register
class SqueezeWatch:
    name = "B_squeeze_watch"
    direction = "long"
    instrument = "equity_or_call_spread"

    min_short_interest_pct = 15.0
    max_float_shares = 150_000_000

    def qualifies(self, cand: Candidate, ctx: StrategyContext) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        blockers: list[str] = []
        if not cand.all_green:
            blockers.append("gates_not_all_green")

        if ctx.short_interest_pct < self.min_short_interest_pct:
            blockers.append(f"short_interest<{self.min_short_interest_pct}")
        else:
            reasons.append(f"short_interest={ctx.short_interest_pct:.1f}%")

        if ctx.float_shares and ctx.float_shares > self.max_float_shares:
            blockers.append("float_too_large")
        else:
            reasons.append("float_ok")

        if ctx.call_imbalance <= 0:
            blockers.append("no_call_imbalance")
        else:
            reasons.append(f"call_imbalance={ctx.call_imbalance:.2f}")

        return (len(blockers) == 0), (reasons if not blockers else blockers)
