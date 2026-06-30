import math

from rmas.backtest.costs import CostConfig, equity_net_return, option_net_return
from rmas.backtest.metrics import (
    compute_metrics,
    max_drawdown,
    profit_factor,
    sortino_ratio,
)


def test_costs_reduce_return():
    cfg = CostConfig(equity_slippage_bps=20)
    gross = (110 - 100) / 100
    net = equity_net_return(100, 110, 100, cfg, "long")
    assert net < gross
    assert net > 0


def test_costs_make_tiny_edge_negative():
    cfg = CostConfig(equity_slippage_bps=50)
    # +0.1% gross is eaten by costs
    net = equity_net_return(100, 100.1, 100, cfg, "long")
    assert net < 0


def test_option_iv_crush_hurts():
    cfg = CostConfig()
    no_crush = option_net_return(2.0, 2.5, bid=1.95, ask=2.05, contracts=5, cfg=cfg, iv_crush=False)
    crush = option_net_return(2.0, 2.5, bid=1.95, ask=2.05, contracts=5, cfg=cfg, iv_crush=True)
    assert crush < no_crush


def test_profit_factor():
    assert profit_factor([0.1, -0.05, 0.2, -0.1]) == (0.3 / 0.15)
    assert profit_factor([0.1, 0.2]) == math.inf


def test_max_drawdown():
    curve = [1.0, 1.2, 0.9, 1.1]
    dd = max_drawdown(curve)
    assert abs(dd - (1.2 - 0.9) / 1.2) < 1e-9


def test_sortino_positive_for_good_series():
    assert sortino_ratio([0.05, 0.04, -0.01, 0.03]) > 0


def test_compute_metrics_targets_ev():
    rs = [0.05, -0.02, 0.08, -0.03, 0.06]
    m = compute_metrics(rs)
    assert m.n_trades == 5
    assert abs(m.expected_value - sum(rs) / len(rs)) < 1e-9
    assert m.profit_factor > 1


def test_empty_metrics_safe():
    m = compute_metrics([])
    assert m.n_trades == 0
    assert m.expected_value == 0
