from datetime import datetime, timedelta, timezone

from rmas.backtest.costs import CostConfig
from rmas.backtest.engine import Backtester, ExitPolicy, Signal
from rmas.backtest.meta_labeling import MetaLabeler
from rmas.types import Bar


def _bars(prices, start=None):
    start = start or (datetime.now(timezone.utc) - timedelta(days=len(prices)))
    bars = []
    for i, p in enumerate(prices):
        bars.append(Bar(start + timedelta(days=i), p, p * 1.01, p * 0.99, p, 1_000_000))
    return bars


def test_winning_long_trade_positive_return():
    prices = [100, 101, 103, 106, 110, 115, 120]
    bars = _bars(prices)
    sig = Signal(ticker="UP", t=bars[0].t, direction="long", atr=2.0)
    bt = Backtester(CostConfig(), ExitPolicy(time_stop_days=5))
    res = bt.run([sig], {"UP": bars})
    assert len(res) == 1
    assert res[0].net_return > 0
    assert res[0].mfe >= res[0].net_return


def test_stop_loss_caps_loss():
    prices = [100, 99, 97, 94, 90, 88]      # steady decline -> stop hit
    bars = _bars(prices)
    sig = Signal(ticker="DN", t=bars[0].t, direction="long", atr=2.0)
    bt = Backtester(CostConfig(), ExitPolicy(atr_stop_mult=2.0, time_stop_days=10))
    res = bt.run([sig], {"DN": bars})
    assert res[0].net_return < 0
    assert res[0].exit_reason in ("stop", "trailing_stop")


def test_no_lookahead_fills_next_open():
    prices = [100, 105, 110]
    bars = _bars(prices)
    sig = Signal(ticker="X", t=bars[0].t, direction="long", atr=1.0)
    bt = Backtester(CostConfig(), ExitPolicy(), fill_at_next_open=True)
    res = bt.run([sig], {"X": bars})
    # entry must be the *next* bar's open (105), not the signal bar (100)
    assert res[0].entry == 105.0


def test_meta_labeler_learns_separable():
    # y=1 when feature high, y=0 when low -> classifier should separate
    X = [[2.0, 0.1], [2.2, 0.1], [1.9, 0.1], [-2.0, 0.1], [-2.1, 0.1], [-1.8, 0.1]]
    y = [1, 1, 1, 0, 0, 0]
    model = MetaLabeler(["mom", "atr"]).fit(X, y, epochs=600)
    assert model.predict_proba({"mom": 2.0, "atr": 0.1}) > 0.6
    assert model.predict_proba({"mom": -2.0, "atr": 0.1}) < 0.4
