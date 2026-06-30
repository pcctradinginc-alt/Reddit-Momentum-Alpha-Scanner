"""Strategy D — Blow-Off Avoid/Fade.

Primary role: a *blocker*. It vetoes longs that are parabolic / euphoric /
showing reddit-decay-while-price-up. A small defined-risk counter-trade is
optional and OFF by default (opt-in via config).
"""

from __future__ import annotations

from dataclasses import dataclass

from rmas.strategies.base import StrategyContext, register
from rmas.types import Candidate


@dataclass
class BlowOffSignal:
    is_blowoff: bool
    reasons: list[str]


def detect_blowoff(ctx: StrategyContext, cand: Candidate,
                   parabolic_pct: float = 60.0, iv_explosion_rank: float = 90.0,
                   mainstream: bool = False, reddit_decay_price_up: bool = False) -> BlowOffSignal:
    reasons: list[str] = []
    if ctx.parabolic_5d_pct >= parabolic_pct:
        reasons.append(f"parabolic_5d={ctx.parabolic_5d_pct:.0f}%")
    iv_rank = cand.features.get("_raw_iv_rank", 0.0)
    if iv_rank >= iv_explosion_rank:
        reasons.append(f"iv_explosion={iv_rank:.0f}")
    if mainstream:
        reasons.append("mainstream_coverage")
    if reddit_decay_price_up:
        reasons.append("reddit_decay_while_price_up")
    return BlowOffSignal(is_blowoff=len(reasons) > 0, reasons=reasons)


@register
class BlowOffFade:
    name = "D_blowoff_fade"
    direction = "short"
    instrument = "equity_or_put_spread"
    role = "blocker"
    allow_counter_trade = False

    def qualifies(self, cand: Candidate, ctx: StrategyContext) -> tuple[bool, list[str]]:
        sig = detect_blowoff(ctx, cand)
        if not self.allow_counter_trade:
            # As a blocker it never *enters*; it only reports the blow-off.
            return False, (["blocker_only"] + sig.reasons)
        if sig.is_blowoff and cand.tradeability and cand.tradeability.green:
            return True, sig.reasons
        return False, sig.reasons

    def blocks_long(self, cand: Candidate, ctx: StrategyContext) -> tuple[bool, list[str]]:
        sig = detect_blowoff(ctx, cand)
        return sig.is_blowoff, sig.reasons
