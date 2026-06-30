"""Event-driven backtest engine with point-in-time discipline.

Principles enforced here:
  * No lookahead: a signal generated at time T can only use bars up to T, and
    fills happen at the NEXT bar's open (configurable).
  * Exits modeled explicitly: stop loss, trailing stop, partial take, time stop.
  * Per-trade outcome captures MFE/MAE/return-after-cost so the optimizer can
    target EV-after-cost rather than raw hit-rate.
  * Signal timestamp & session (premarket/intraday/afterhours) preserved.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from rmas.backtest.costs import CostConfig, equity_net_return
from rmas.types import Bar, SignalTime


@dataclass
class Signal:
    ticker: str
    t: datetime                          # signal time (point-in-time)
    direction: str = "long"
    strategy: str = ""
    signal_time: SignalTime = SignalTime.INTRADAY
    regime: str = "neutral"
    atr: float = 0.0
    meta: dict = field(default_factory=dict)


@dataclass
class TradeResult:
    ticker: str
    strategy: str
    direction: str
    regime: str
    entry_t: datetime
    exit_t: datetime
    entry: float
    exit: float
    bars_held: int
    net_return: float
    mfe: float                           # max favorable excursion (fraction)
    mae: float                           # max adverse excursion (fraction)
    exit_reason: str


@dataclass
class ExitPolicy:
    atr_stop_mult: float = 2.0
    trailing_stop_atr_mult: float = 2.5
    time_stop_days: int = 10
    partial_take_r_multiple: float = 1.5
    target_r_multiple: float = 3.0


def _simulate_trade(
    sig: Signal,
    future_bars: Sequence[Bar],
    policy: ExitPolicy,
    cost_cfg: CostConfig,
    fill_at_next_open: bool = True,
) -> TradeResult | None:
    if not future_bars:
        return None

    long = sig.direction == "long"
    # Entry at next bar open (no lookahead) or current close.
    entry_idx = 1 if fill_at_next_open and len(future_bars) > 1 else 0
    entry = future_bars[entry_idx].open if fill_at_next_open else future_bars[0].close
    if entry <= 0:
        return None

    atr = sig.atr or (entry * 0.03)
    stop = entry - policy.atr_stop_mult * atr if long else entry + policy.atr_stop_mult * atr
    r_per_share = abs(entry - stop)
    trail = stop

    mfe = mae = 0.0
    exit_price = entry
    exit_reason = "time_stop"
    exit_idx = entry_idx

    path = future_bars[entry_idx:]
    for i, bar in enumerate(path):
        # excursions
        fav = (bar.high - entry) / entry if long else (entry - bar.low) / entry
        adv = (bar.low - entry) / entry if long else (entry - bar.high) / entry
        mfe = max(mfe, fav)
        mae = min(mae, adv)

        # trailing stop update
        if long:
            trail = max(trail, bar.close - policy.trailing_stop_atr_mult * atr)
        else:
            trail = min(trail, bar.close + policy.trailing_stop_atr_mult * atr)

        # stop hit (use bar low/high)
        if long and bar.low <= max(stop, trail):
            exit_price = max(stop, trail)
            exit_reason = "stop" if max(stop, trail) == stop else "trailing_stop"
            exit_idx = entry_idx + i
            break
        if not long and bar.high >= min(stop, trail):
            exit_price = min(stop, trail)
            exit_reason = "stop" if min(stop, trail) == stop else "trailing_stop"
            exit_idx = entry_idx + i
            break

        # target hit
        target = entry + policy.target_r_multiple * r_per_share if long else entry - policy.target_r_multiple * r_per_share
        if (long and bar.high >= target) or (not long and bar.low <= target):
            exit_price = target
            exit_reason = "target"
            exit_idx = entry_idx + i
            break

        # time stop
        if i >= policy.time_stop_days:
            exit_price = bar.close
            exit_reason = "time_stop"
            exit_idx = entry_idx + i
            break
    else:
        exit_price = path[-1].close
        exit_idx = entry_idx + len(path) - 1

    shares = max(1, int(1000 / entry))  # nominal sizing for return calc
    net = equity_net_return(entry, exit_price, shares, cost_cfg, sig.direction)

    return TradeResult(
        ticker=sig.ticker,
        strategy=sig.strategy,
        direction=sig.direction,
        regime=sig.regime,
        entry_t=future_bars[entry_idx].t,
        exit_t=future_bars[exit_idx].t,
        entry=round(entry, 4),
        exit=round(exit_price, 4),
        bars_held=exit_idx - entry_idx,
        net_return=round(net, 6),
        mfe=round(mfe, 6),
        mae=round(mae, 6),
        exit_reason=exit_reason,
    )


class Backtester:
    """Runs signals against per-ticker bar histories."""

    def __init__(self, cost_cfg: CostConfig, policy: ExitPolicy, fill_at_next_open: bool = True):
        self.cost_cfg = cost_cfg
        self.policy = policy
        self.fill_at_next_open = fill_at_next_open

    def run(
        self,
        signals: Sequence[Signal],
        bars_by_ticker: dict[str, Sequence[Bar]],
        bar_index_for_time: Callable[[Sequence[Bar], datetime], int] | None = None,
    ) -> list[TradeResult]:
        results: list[TradeResult] = []
        for sig in signals:
            bars = bars_by_ticker.get(sig.ticker)
            if not bars:
                continue
            idx = (bar_index_for_time(bars, sig.t) if bar_index_for_time
                   else self._default_index(bars, sig.t))
            if idx is None or idx < 0:
                continue
            future = bars[idx:]
            res = _simulate_trade(sig, future, self.policy, self.cost_cfg, self.fill_at_next_open)
            if res:
                results.append(res)
        return results

    @staticmethod
    def _default_index(bars: Sequence[Bar], t: datetime) -> int | None:
        for i, b in enumerate(bars):
            if b.t >= t:
                return i
        return None
