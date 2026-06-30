"""Shared gate utilities: color assignment from score + hard blockers."""

from __future__ import annotations

from rmas.types import GateColor, GateResult


def color_from(score: float, min_score: float, blockers: list[str]) -> GateColor:
    """GREEN only if score clears the bar AND no hard blockers fired."""
    if blockers:
        return GateColor.RED
    if score >= min_score:
        return GateColor.GREEN
    if score >= min_score * 0.8:
        return GateColor.YELLOW
    return GateColor.RED


def make_gate(name: str, score: float, min_score: float,
              reasons: list[str], blockers: list[str]) -> GateResult:
    return GateResult(
        name=name,
        score=round(float(score), 4),
        color=color_from(score, min_score, blockers),
        reasons=reasons,
        blockers=blockers,
    )
