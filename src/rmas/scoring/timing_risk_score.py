"""Timing/Risk gate: only enter on a real trigger, and never into a blow-off.

Combines three sub-scores:
  * entry_trigger   — is there a concrete trigger (breakout / pullback-VWAP /
    opening-range-breakout / post-earnings-drift / squeeze ignition)?
  * not_overheated  — FOMO / blow-off filter (higher = calmer).
  * attention_intact— reddit/user growth still alive (not decaying).

Hard blockers: extreme intraday gap, parabolic 5d move, mainstream coverage,
attention already decaying.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rmas.mathx import clip, weighted_score
from rmas.scoring.gates import make_gate
from rmas.types import GateResult


@dataclass
class TimingInput:
    entry_triggers_fired: list[str] = field(default_factory=list)
    intraday_gap_pct: float = 0.0          # percent, e.g. 4.0 = +4%
    parabolic_5d_pct: float = 0.0          # percent move over 5 sessions
    iv_rank: float = 0.0
    reddit_decaying: bool = False
    price_up_while_reddit_down: bool = False
    mainstream_coverage: bool = False
    is_top_hype_ticker: bool = False


def _entry_score(triggers: list[str]) -> float:
    # More confirming triggers -> higher, saturating quickly.
    return clip(len(triggers) / 2.0)


def _overheat_score(inp: TimingInput, max_gap: float, max_parabolic: float) -> float:
    """1.0 = calm, 0.0 = blow-off."""
    penalty = 0.0
    penalty += clip(inp.intraday_gap_pct / max_gap) * 0.35
    penalty += clip(inp.parabolic_5d_pct / max_parabolic) * 0.35
    penalty += clip((inp.iv_rank - 70) / 30.0) * 0.15
    if inp.is_top_hype_ticker:
        penalty += 0.15
    return clip(1.0 - penalty)


def score_timing_risk(inp: TimingInput, cfg) -> GateResult:
    weights = cfg.weights.to_dict() if hasattr(cfg.weights, "to_dict") else dict(cfg.weights)
    gate = cfg.gate

    max_gap = gate.get("max_intraday_gap_pct", 12)
    max_parabolic = gate.get("max_parabolic_5d_pct", 60)

    sub = {
        "entry_trigger": _entry_score(inp.entry_triggers_fired),
        "not_overheated": _overheat_score(inp, max_gap, max_parabolic),
        "attention_intact": 0.0 if inp.reddit_decaying else 1.0,
    }
    score = weighted_score(sub, weights)

    reasons: list[str] = []
    blockers: list[str] = []

    if inp.entry_triggers_fired:
        reasons.append("triggers=" + ",".join(inp.entry_triggers_fired))
    else:
        blockers.append("no_entry_trigger")

    if inp.intraday_gap_pct > max_gap:
        blockers.append(f"gap={inp.intraday_gap_pct:.1f}%>{max_gap}%")
    if inp.parabolic_5d_pct > max_parabolic:
        blockers.append(f"parabolic_5d={inp.parabolic_5d_pct:.1f}%>{max_parabolic}%")
    if gate.get("block_if_mainstream", True) and inp.mainstream_coverage:
        blockers.append("mainstream_coverage")
    if gate.get("require_attention_not_decaying", True) and inp.reddit_decaying:
        blockers.append("attention_decaying")
    if inp.price_up_while_reddit_down:
        blockers.append("price_up_reddit_down")  # classic late/blow-off divergence

    return make_gate("timing_risk", score, gate.get("min_score", 0.55), reasons, blockers)
