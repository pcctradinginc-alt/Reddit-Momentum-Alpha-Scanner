from datetime import datetime, timedelta, timezone

from rmas.features.momentum import (
    MomentumInput,
    build_momentum_features,
    is_parabolic,
    vwap,
)
from rmas.types import Bar


def _uptrend(n=40, start=100.0, step=1.0):
    bars = []
    now = datetime.now(timezone.utc)
    price = start
    for i in range(n):
        o = price
        c = price + step
        bars.append(Bar(now - timedelta(days=n - i), o, c + 0.5, o - 0.5, c, 1_000_000 + i * 10_000))
        price = c
    return bars


def test_uptrend_breaks_out_and_strong_rs():
    bars = _uptrend()
    # last bar gets a volume surge
    bars[-1] = Bar(bars[-1].t, bars[-1].open, bars[-1].high, bars[-1].low,
                   bars[-1].close, 5_000_000)
    inp = MomentumInput("UP", bars, {"SPY": 0.0, "QQQ": 0.0, "SECTOR": 0.0})
    feats = build_momentum_features(inp)
    assert feats["_raw_rel_strength"] > 0
    assert feats["_raw_breakout_pct"] > 0          # new-high territory
    assert feats["_raw_rel_volume"] > 1.5
    assert feats["rel_strength_vs_spy"] > 0.5


def test_short_history_returns_neutral_low():
    bars = _uptrend(n=10)
    feats = build_momentum_features(MomentumInput("S", bars, {"SPY": 0.0}))
    assert feats["breakout_20d"] == 0.0


def test_vwap_between_high_low():
    bars = _uptrend(n=25)
    vw = vwap(bars, 20)
    assert min(b.low for b in bars[-20:]) <= vw <= max(b.high for b in bars[-20:])


def test_is_parabolic():
    assert is_parabolic(0.8, 60.0)       # +80% in 5d
    assert not is_parabolic(0.10, 60.0)
