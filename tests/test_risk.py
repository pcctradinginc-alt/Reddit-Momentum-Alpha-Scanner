from rmas.config import load_config
from rmas.risk.limits import PortfolioState, check_limits
from rmas.risk.position_sizing import size_position

CFG = load_config()


def test_sizing_risks_fixed_fraction():
    res = size_position(entry=100, atr_value=2.0, account_equity=100_000,
                        risk_per_trade_pct=1.0, atr_stop_mult=2.0, direction="long")
    # risk budget = $1000; r/share = 2*2 = $4 -> 250 shares, risk ~ $1000
    assert res.shares == 250
    assert abs(res.risk_usd - 1000) < 1e-6
    assert res.stop == 96.0


def test_sizing_notional_cap():
    res = size_position(entry=10, atr_value=0.01, account_equity=100_000,
                        risk_per_trade_pct=1.0, atr_stop_mult=2.0,
                        direction="long", max_notional_pct=20.0)
    assert res.notional_usd <= 20_000 + 1e-6


def test_sizing_regime_scaling():
    full = size_position(100, 2.0, account_equity=100_000, risk_per_trade_pct=1.0,
                         atr_stop_mult=2.0, regime_multiplier=1.0)
    half = size_position(100, 2.0, account_equity=100_000, risk_per_trade_pct=1.0,
                         atr_stop_mult=2.0, regime_multiplier=0.5)
    assert half.shares < full.shares


def test_limits_block_on_daily_loss():
    state = PortfolioState(account_equity=100_000, realized_pnl_today=-2_500)
    d = check_limits(state, proposed_notional=5000, sector="tech", is_meme=False,
                     cfg=CFG.risk)
    assert not d.allowed
    assert "daily_loss_limit_hit" in d.blockers


def test_limits_block_on_concurrency():
    state = PortfolioState(account_equity=100_000, open_trades=4)
    d = check_limits(state, proposed_notional=5000, sector="tech", is_meme=False,
                     cfg=CFG.risk)
    assert "max_concurrent_trades" in d.blockers


def test_limits_scale_for_sector_room():
    # nearly full sector -> scale < 1
    state = PortfolioState(account_equity=100_000,
                           sector_exposure_usd={"tech": 33_000})
    d = check_limits(state, proposed_notional=10_000, sector="tech", is_meme=False,
                     cfg=CFG.risk)
    assert d.scale < 1.0
