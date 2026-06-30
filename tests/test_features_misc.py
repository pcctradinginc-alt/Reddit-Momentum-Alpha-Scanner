from datetime import datetime, timezone

from rmas.features.catalyst import detect_catalyst
from rmas.features.cross_source import CrossSourceInput, cross_source_score
from rmas.features.lead_user import AuthorRecord, LeadUserModel
from rmas.features.options_flow import build_options_features, iv_overpriced
from rmas.features.regime import Regime, RegimeInput, classify_regime
from rmas.types import OptionsSnapshot


def test_catalyst_detected_vs_meme():
    real = detect_catalyst(["earnings beat and raised guidance"], ["Company raises guidance"])
    meme = detect_catalyst(["to the moon 🚀 buy buy buy"], [])
    assert real.has_catalyst and not real.is_meme_only
    assert meme.is_meme_only and not meme.has_catalyst
    assert real.score > meme.score


def test_cross_source_prefers_divergence():
    early = cross_source_score(CrossSourceInput(reddit_attention_z=2.5, google_trends_z=0.0,
                                                x_attention_z=0.0, news_count_z=0.0))
    late = cross_source_score(CrossSourceInput(reddit_attention_z=2.0, google_trends_z=2.0,
                                               x_attention_z=2.0, news_count_z=2.0))
    assert early["cross_source_earliness"] > late["cross_source_earliness"]


def test_options_imbalance_feature():
    snap = OptionsSnapshot("ABC", datetime.now(timezone.utc), call_volume=9000,
                           put_volume=1000, oi_change_pct=0.4, unusual_call_activity=True)
    feats = build_options_features(snap)
    assert feats["options_call_imbalance"] > 0.6
    assert feats["_options_available"] == 1.0


def test_iv_overpriced():
    assert iv_overpriced(95, 80, 92, 95)
    assert not iv_overpriced(50, 50, 92, 95)


def test_regime_risk_off_blocks():
    st = classify_regime(RegimeInput(vix=35, index_above_200dma=False,
                                     index_20d_return=-0.05, breadth_pct_above_50dma=30))
    assert st.regime in (Regime.RISK_OFF, Regime.MOMENTUM_CRASH)
    assert st.block_new_longs


def test_regime_risk_on_full_size():
    st = classify_regime(RegimeInput(vix=13, index_above_200dma=True,
                                     index_20d_return=0.03, breadth_pct_above_50dma=65))
    assert st.regime == Regime.RISK_ON
    assert st.size_multiplier == 1.0


def test_lead_user_shrinkage_and_pump_penalty():
    model = LeadUserModel()
    model.records["sharp"] = AuthorRecord("sharp", n_calls=50, avg_forward_return=0.06,
                                          early_hit_rate=0.7, pump_rate=0.0)
    model.records["pumper"] = AuthorRecord("pumper", n_calls=50, avg_forward_return=0.06,
                                           early_hit_rate=0.7, pump_rate=0.9)
    model.records["noob"] = AuthorRecord("noob", n_calls=1, avg_forward_return=0.20,
                                         early_hit_rate=1.0, pump_rate=0.0)
    assert model.reputation("sharp") > model.reputation("pumper")
    # tiny sample shrinks toward ~0 despite huge avg return
    assert model.reputation("noob") < model.reputation("sharp")
