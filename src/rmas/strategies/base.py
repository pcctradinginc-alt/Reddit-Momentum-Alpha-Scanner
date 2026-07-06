"""Strategy base: shared qualification + trade-plan construction.

A strategy inspects a scored Candidate (+ context) and decides whether it
qualifies, then builds a concrete, risk-defined TradePlan. The Blow-Off
strategy is special: it mainly *vetoes* longs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from rmas.risk.position_sizing import size_position
from rmas.types import Candidate, TradePlan

STRATEGY_REGISTRY: dict[str, "Strategy"] = {}


@dataclass
class StrategyContext:
    close: float
    atr: float
    short_interest_pct: float = 0.0
    float_shares: float = 0.0
    days_since_earnings: int | None = None
    call_imbalance: float = 0.0
    parabolic_5d_pct: float = 0.0
    regime_multiplier: float = 1.0
    account_equity: float = 100_000.0
    risk_per_trade_pct: float = 0.75
    atr_stop_mult: float = 2.0
    is_meme: bool = False
    extras: dict = field(default_factory=dict)


class Strategy(Protocol):
    name: str
    direction: str
    instrument: str

    def qualifies(self, cand: Candidate, ctx: StrategyContext) -> tuple[bool, list[str]]: ...


def register(cls):
    inst = cls()
    STRATEGY_REGISTRY[inst.name] = inst
    return cls


def build_trade_plan(cand: Candidate, ctx: StrategyContext, strat: Strategy,
                     time_stop_days: int = 7, exit_note: str = "") -> TradePlan:
    """Construct the risk-defined plan + human-readable rationale."""
    entry = ctx.close
    sizing = size_position(
        entry=entry,
        atr_value=ctx.atr,
        account_equity=ctx.account_equity,
        risk_per_trade_pct=ctx.risk_per_trade_pct,
        atr_stop_mult=ctx.atr_stop_mult,
        direction=strat.direction,
        regime_multiplier=ctx.regime_multiplier,
    )
    r = sizing.r_per_share
    if strat.direction == "long":
        targets = [round(entry + m * r, 2) for m in (1.5, 3.0)]
    else:
        targets = [round(entry - m * r, 2) for m in (1.5, 3.0)]

    disc = cand.discovery.score if cand.discovery else 0.0
    trade = cand.tradeability.score if cand.tradeability else 0.0
    timing = cand.timing_risk.score if cand.timing_risk else 0.0
    # crude expected edge proxy (bps): blend of gate scores minus a cost drag.
    edge_bps = round((0.4 * disc + 0.35 * trade + 0.25 * timing) * 300 - 20, 1)

    # If the trade is executed with options instead of shares: short-dated
    # calls are theta destruction for a 1-10 day momentum hold.
    iv = cand.features.get("_raw_iv_rank", 0.0)
    opt_note = "If using options: 30-60 DTE, delta 0.6-0.7, exit with >=20 DTE left"
    if iv and iv > 60:
        opt_note += f"; IV rank {iv:.0f} is rich -> prefer a debit spread"

    rationale = {
        "why_signal": _why_signal(cand),
        "why_tradeable": _why_tradeable(cand),
        "why_now": _why_now(cand),
        "risk": f"Stop {sizing.stop} ({ctx.atr_stop_mult}x ATR), risk ${sizing.risk_usd}",
        "exit": (f"Targets {targets}; time stop {time_stop_days}d; trailing stop on close."
                 + (f" {exit_note}" if exit_note else "")),
        "options": opt_note,
        "edge": f"~{edge_bps} bps expected edge after modeled costs (gate-blended).",
    }

    return TradePlan(
        ticker=cand.ticker,
        strategy=strat.name,
        direction=strat.direction,
        instrument=strat.instrument,
        entry=round(entry, 2),
        stop=sizing.stop,
        targets=targets,
        shares=sizing.shares,
        risk_usd=sizing.risk_usd,
        r_multiple_target=3.0,
        time_stop_days=time_stop_days,
        expected_edge_bps=edge_bps,
        rationale=rationale,
        features=dict(cand.features),
    )


def _why_signal(c: Candidate) -> str:
    g = c.discovery
    if not g:
        return "discovery n/a"
    return f"Early attention acceleration (discovery {g.score:.2f}: {'; '.join(g.reasons[:3])})"


def _why_tradeable(c: Candidate) -> str:
    g = c.tradeability
    if not g:
        return "tradeability n/a"
    return f"Price/volume/options confirm (tradeability {g.score:.2f}: {'; '.join(g.reasons[:3])})"


def _why_now(c: Candidate) -> str:
    g = c.timing_risk
    if not g:
        return "timing n/a"
    return f"Trigger fired & not overheated (timing {g.score:.2f}: {'; '.join(g.reasons[:2])})"
