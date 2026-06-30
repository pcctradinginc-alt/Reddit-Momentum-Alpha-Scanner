"""Adapter protocols + offline/synthetic data generators.

The Protocols define the contract the pipeline depends on. The synthetic
generators are seeded by ticker so output is deterministic (reproducible tests
and demos) while still looking plausibly market-like.
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

from rmas.types import (
    Bar,
    LiquiditySnapshot,
    Mention,
    OptionsSnapshot,
)


def _seed_for(ticker: str, salt: str = "") -> int:
    h = hashlib.sha256(f"{ticker}|{salt}".encode()).hexdigest()
    return int(h[:8], 16)


# --------------------------------------------------------------------------- #
# Protocols
# --------------------------------------------------------------------------- #
@runtime_checkable
class RedditSource(Protocol):
    def fetch_mentions(self, subreddits: list[str], lookback_hours: int) -> list[Mention]: ...


@runtime_checkable
class MarketSource(Protocol):
    def daily_bars(self, ticker: str, lookback_days: int) -> list[Bar]: ...
    def liquidity(self, ticker: str) -> LiquiditySnapshot: ...


@runtime_checkable
class OptionsSource(Protocol):
    def options_snapshot(self, ticker: str) -> OptionsSnapshot: ...


# --------------------------------------------------------------------------- #
# Synthetic generators (offline mode)
# --------------------------------------------------------------------------- #
class SyntheticMarket:
    """Deterministic synthetic OHLCV + liquidity, seeded per ticker."""

    def daily_bars(self, ticker: str, lookback_days: int = 60) -> list[Bar]:
        rng = random.Random(_seed_for(ticker, "bars"))
        price = 10 + rng.random() * 200
        drift = (rng.random() - 0.4) * 0.004
        vol = 0.015 + rng.random() * 0.03
        base_volume = 1_000_000 + rng.random() * 8_000_000

        # A deterministic minority of names are genuinely "trending" — recent
        # uptrend + a final-bar volume surge — so the Tradeability gate confirms
        # them while the rest are (correctly) culled. This keeps the offline demo
        # representative without making every name a buy.
        hot = (_seed_for(ticker, "hot") % 100) < 35

        bars: list[Bar] = []
        now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        for i in range(lookback_days):
            t = now - timedelta(days=lookback_days - i)
            local_drift = drift
            if hot and i >= lookback_days - 6:        # last ~week pushes higher
                local_drift = drift + 0.012
            shock = rng.gauss(local_drift, vol)
            open_ = price
            close = max(0.5, price * (1 + shock))
            high = max(open_, close) * (1 + abs(rng.gauss(0, vol / 2)))
            low = min(open_, close) * (1 - abs(rng.gauss(0, vol / 2)))
            volume = base_volume * (0.6 + rng.random())
            if hot and i == lookback_days - 1:        # final-bar relative-volume surge
                volume = base_volume * 2.8
            bars.append(Bar(t, round(open_, 2), round(high, 2), round(low, 2), round(close, 2), round(volume)))
            price = close
        return bars

    def liquidity(self, ticker: str) -> LiquiditySnapshot:
        rng = random.Random(_seed_for(ticker, "liq"))
        bars = self.daily_bars(ticker, 5)
        last = bars[-1]
        shares_out = 20_000_000 + rng.random() * 400_000_000
        return LiquiditySnapshot(
            ticker=ticker,
            market_cap_usd=last.close * shares_out,
            dollar_volume_usd=last.close * last.volume,
            bid_ask_spread_bps=5 + rng.random() * 40,
            short_interest_pct=rng.random() * 30,
            float_shares=shares_out * (0.5 + rng.random() * 0.5),
            borrow_available=rng.random() > 0.1,
        )


class SyntheticOptions:
    def options_snapshot(self, ticker: str) -> OptionsSnapshot:
        rng = random.Random(_seed_for(ticker, "opt"))
        cv = 1000 + rng.random() * 50000
        pv = 1000 + rng.random() * 40000
        return OptionsSnapshot(
            ticker=ticker,
            asof=datetime.now(timezone.utc),
            call_volume=cv,
            put_volume=pv,
            call_oi=cv * (2 + rng.random() * 5),
            put_oi=pv * (2 + rng.random() * 5),
            oi_change_pct=(rng.random() - 0.3) * 0.8,
            iv_rank=rng.random() * 100,
            iv_percentile=rng.random() * 100,
            skew=(rng.random() - 0.5) * 10,
            atm_spread_bps=10 + rng.random() * 80,
            unusual_call_activity=rng.random() > 0.7,
        )


class SyntheticReddit:
    """Deterministic synthetic mentions for offline demos/tests."""

    def __init__(self, tickers: list[str] | None = None):
        self.tickers = tickers or ["NVDA", "AMD", "GME", "PLTR", "SOFI", "TSLA", "AI", "MARA"]

    _TEMPLATES = [
        "${t} setup looks clean after the {a} reclaim, watching for follow through",
        "anyone else in ${t}? volume picked up and it held the {a}",
        "${t} calls into earnings, {a} flow is interesting here",
        "thinking about ${t} for a swing, {a} breakout over the 20d",
        "${t} short interest is high, {a} could squeeze if it breaks out",
        "added to ${t} today, {a} relative strength vs the market is solid",
        "${t} pulling back to vwap, {a} might be a decent entry",
        "not financial advice but ${t} {a} chart is setting up",
    ]
    _ADJ = ["the daily", "premarket", "options", "the gap", "RS", "the float",
            "intraday", "after hours", "the catalyst", "the trend"]

    def fetch_mentions(self, subreddits: list[str], lookback_hours: int = 24) -> list[Mention]:
        out: list[Mention] = []
        now = datetime.now(timezone.utc)
        for tk in self.tickers:
            rng = random.Random(_seed_for(tk, "reddit"))
            n = 3 + rng.randint(0, 30)
            for _ in range(n):
                age_h = rng.random() * lookback_hours
                tmpl = rng.choice(self._TEMPLATES)
                text = tmpl.format(t=tk, a=rng.choice(self._ADJ)) + " 🚀" * rng.randint(0, 2)
                out.append(Mention(
                    ticker=tk,
                    # distinct authors (one per post mostly) -> organic-looking
                    author=f"u_{tk.lower()}_{rng.randint(1, max(6, n * 3))}",
                    subreddit=rng.choice(subreddits or ["wallstreetbets"]),
                    created_utc=now - timedelta(hours=age_h),
                    text=text,
                    score=rng.randint(0, 500),
                    is_submission=rng.random() > 0.6,
                    author_age_days=60 + rng.random() * 740,
                ))
        return out


def synthetic_attention_series(ticker: str, days: int = 30) -> list[float]:
    """A plausible *baseline* mention-per-day history (no tail spike).

    The pipeline injects "today's" observed mention count on top of this
    baseline, so genuine acceleration comes from the live count vs. this
    quiet history — exactly the early-attention signal we want to detect.
    """
    rng = random.Random(_seed_for(ticker, "attn"))
    base = 2 + rng.random() * 5
    return [round(max(0.0, base + rng.gauss(0, 1.0)), 2) for _ in range(days)]
