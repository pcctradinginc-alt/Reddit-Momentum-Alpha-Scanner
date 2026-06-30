from rmas.config import load_config
from rmas.scoring.discovery_score import score_discovery
from rmas.scoring.timing_risk_score import TimingInput, score_timing_risk
from rmas.scoring.tradeability_score import score_tradeability
from rmas.types import GateColor, LiquiditySnapshot

CFG = load_config()


def _good_discovery_feats():
    return {
        "mention_z_7d": 0.9, "mention_z_30d": 0.85, "mention_acceleration": 0.8,
        "new_threads_per_hour_z": 0.8, "comments_per_hour_z": 0.8,
        "unique_authors_z": 0.8, "author_growth": 0.8, "subreddit_diversity": 0.8,
        "_raw_mention_z_7d": 2.5, "_raw_unique_authors": 12, "_raw_bot_ratio": 0.1,
    }


def test_discovery_green_when_strong():
    g = score_discovery(_good_discovery_feats(), CFG.discovery)
    assert g.color == GateColor.GREEN
    assert g.green


def test_discovery_blocked_by_high_bot_ratio():
    feats = _good_discovery_feats()
    feats["_raw_bot_ratio"] = 0.9
    g = score_discovery(feats, CFG.discovery)
    assert not g.green
    assert any("bot_ratio" in b for b in g.blockers)


def test_discovery_blocked_by_too_few_authors():
    feats = _good_discovery_feats()
    feats["_raw_unique_authors"] = 2
    g = score_discovery(feats, CFG.discovery)
    assert not g.green


def _good_trade_feats():
    return {
        "rel_strength_vs_spy": 0.8, "rel_strength_vs_sector": 0.8, "breakout_20d": 0.8,
        "above_vwap": 0.7, "above_ema20": 0.7, "rel_volume": 0.8,
        "premarket_volume_z": 0.6, "options_call_imbalance": 0.8,
        "_raw_rel_strength": 0.05, "_raw_rel_volume": 2.5,
        "_options_available": 1.0, "_raw_iv_rank": 40, "_raw_iv_percentile": 45,
    }


def _good_liq():
    return LiquiditySnapshot("ABC", market_cap_usd=2e9, dollar_volume_usd=5e7,
                             bid_ask_spread_bps=15, short_interest_pct=20,
                             float_shares=5e7, borrow_available=True)


def test_tradeability_green():
    g = score_tradeability(_good_trade_feats(), CFG.tradeability, _good_liq())
    assert g.green


def test_tradeability_blocked_by_iv_overpriced():
    feats = _good_trade_feats()
    feats["_raw_iv_rank"] = 99
    g = score_tradeability(feats, CFG.tradeability, _good_liq())
    assert not g.green
    assert "iv_overpriced" in g.blockers


def test_tradeability_blocked_by_illiquidity():
    liq = LiquiditySnapshot("ABC", market_cap_usd=1e6, dollar_volume_usd=1e5,
                            bid_ask_spread_bps=300)
    g = score_tradeability(_good_trade_feats(), CFG.tradeability, liq)
    assert not g.green


def test_timing_green_with_trigger_and_calm():
    inp = TimingInput(entry_triggers_fired=["breakout_20d", "pullback_vwap"],
                      intraday_gap_pct=2.0, parabolic_5d_pct=10.0, iv_rank=40)
    g = score_timing_risk(inp, CFG.timing_risk)
    assert g.green


def test_timing_blocked_by_parabolic():
    inp = TimingInput(entry_triggers_fired=["breakout_20d"],
                      intraday_gap_pct=2.0, parabolic_5d_pct=120.0, iv_rank=40)
    g = score_timing_risk(inp, CFG.timing_risk)
    assert not g.green
    assert any("parabolic" in b for b in g.blockers)


def test_timing_blocked_when_no_trigger():
    inp = TimingInput(entry_triggers_fired=[], intraday_gap_pct=1.0, parabolic_5d_pct=5.0)
    g = score_timing_risk(inp, CFG.timing_risk)
    assert "no_entry_trigger" in g.blockers


def test_timing_blocked_by_attention_decay():
    inp = TimingInput(entry_triggers_fired=["breakout_20d"], parabolic_5d_pct=5.0,
                      reddit_decaying=True)
    g = score_timing_risk(inp, CFG.timing_risk)
    assert "attention_decaying" in g.blockers
