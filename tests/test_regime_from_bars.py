"""Regime inputs derived from real index bars (no extra data cost)."""

from datetime import datetime, timedelta, timezone

from rmas.features.regime import Regime, classify_regime, regime_input_from_bars
from rmas.types import Bar


def _bars(closes: list[float]) -> list[Bar]:
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [Bar(t=t0 + timedelta(days=i), open=c, high=c * 1.005, low=c * 0.995,
                close=c, volume=1_000_000) for i, c in enumerate(closes)]


def test_calm_uptrend_maps_to_risk_on():
    # steady +0.1%/day grind, tiny noise -> low realized vol, above 200dma
    closes = [400 * (1.001 ** i) for i in range(250)]
    inp = regime_input_from_bars(_bars(closes))
    assert inp.index_above_200dma
    assert inp.index_20d_return > 0
    assert inp.vix < 16                     # near-zero realized vol
    assert inp.breadth_pct_above_50dma > 55
    state = classify_regime(inp)
    assert state.regime == Regime.RISK_ON
    assert not state.block_new_longs


def test_crash_maps_to_risk_off():
    # -1.5%/day with heavy alternating noise -> below 200dma, high vol
    closes = []
    px = 500.0
    for i in range(250):
        px *= 0.985 if i % 2 == 0 else 1.002
        closes.append(px)
    inp = regime_input_from_bars(_bars(closes))
    assert not inp.index_above_200dma
    state = classify_regime(inp)
    assert state.regime in (Regime.RISK_OFF, Regime.MOMENTUM_CRASH)
    assert state.size_multiplier < 1.0


def test_short_history_returns_neutral_defaults():
    inp = regime_input_from_bars(_bars([100.0] * 10))
    assert inp.vix == 20.0
    assert inp.index_above_200dma


def test_qqq_spread_feeds_momentum_factor():
    spy = [400 * (1.0005 ** i) for i in range(30)]
    qqq_up = [300 * (1.004 ** i) for i in range(30)]
    inp = regime_input_from_bars(_bars(spy), _bars(qqq_up))
    assert inp.momentum_factor_5d_return > 0
