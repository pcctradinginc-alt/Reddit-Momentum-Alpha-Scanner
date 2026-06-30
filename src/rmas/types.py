"""Core domain types shared across the pipeline.

Plain stdlib dataclasses (no pydantic) so the engine stays import-light and
trivially testable. Timestamps are timezone-aware UTC by convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SignalTime(str, Enum):
    """When in the session a signal was observed — stored for backtest fidelity."""

    PREMARKET = "premarket"
    INTRADAY = "intraday"
    AFTERHOURS = "afterhours"


class GateColor(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


# --------------------------------------------------------------------------- #
# Reddit ingestion
# --------------------------------------------------------------------------- #
@dataclass
class Mention:
    """A single Reddit post/comment referencing a ticker."""

    ticker: str
    author: str
    subreddit: str
    created_utc: datetime
    text: str
    score: int = 0
    is_submission: bool = False
    permalink: str = ""
    # filled by bot-detection
    author_age_days: float | None = None
    bot_probability: float = 0.0


@dataclass
class MentionStats:
    """Aggregated attention stats for one ticker over a window."""

    ticker: str
    asof: datetime
    mentions: int = 0
    unique_authors: int = 0
    new_threads: int = 0
    comments: int = 0
    subreddits: int = 0
    bot_ratio: float = 0.0
    lead_user_weight: float = 0.0


# --------------------------------------------------------------------------- #
# Market data
# --------------------------------------------------------------------------- #
@dataclass
class Bar:
    """OHLCV bar (daily or intraday)."""

    t: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class OptionsSnapshot:
    """Aggregated options-flow snapshot for a ticker."""

    ticker: str
    asof: datetime
    call_volume: float = 0.0
    put_volume: float = 0.0
    call_oi: float = 0.0
    put_oi: float = 0.0
    oi_change_pct: float = 0.0
    iv_rank: float = 0.0          # 0..100
    iv_percentile: float = 0.0    # 0..100
    skew: float = 0.0             # put_iv - call_iv (vol points)
    atm_spread_bps: float = 0.0
    unusual_call_activity: bool = False

    @property
    def call_put_imbalance(self) -> float:
        tot = self.call_volume + self.put_volume
        return (self.call_volume - self.put_volume) / tot if tot > 0 else 0.0


@dataclass
class LiquiditySnapshot:
    ticker: str
    market_cap_usd: float = 0.0
    dollar_volume_usd: float = 0.0
    bid_ask_spread_bps: float = 0.0
    short_interest_pct: float = 0.0
    float_shares: float = 0.0
    borrow_available: bool = True


# --------------------------------------------------------------------------- #
# Scoring / signals
# --------------------------------------------------------------------------- #
@dataclass
class GateResult:
    name: str
    score: float                       # 0..1
    color: GateColor
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    @property
    def green(self) -> bool:
        return self.color == GateColor.GREEN


@dataclass
class Candidate:
    """A scored ticker flowing through the three gates."""

    ticker: str
    asof: datetime
    signal_time: SignalTime = SignalTime.INTRADAY
    discovery: GateResult | None = None
    tradeability: GateResult | None = None
    timing_risk: GateResult | None = None
    strategy: str = ""
    features: dict[str, float] = field(default_factory=dict)
    meta_probability: float | None = None
    rank_score: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def all_green(self) -> bool:
        return all(
            g is not None and g.green
            for g in (self.discovery, self.tradeability, self.timing_risk)
        )


@dataclass
class TradePlan:
    """The concrete, risk-defined plan attached to an alert."""

    ticker: str
    strategy: str
    direction: str                  # long / short
    instrument: str                 # equity / call_spread / put_spread
    entry: float
    stop: float
    targets: list[float] = field(default_factory=list)
    shares: int = 0
    risk_usd: float = 0.0
    r_multiple_target: float = 0.0
    time_stop_days: int = 0
    expected_edge_bps: float = 0.0
    rationale: dict[str, str] = field(default_factory=dict)
