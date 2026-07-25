from rmas.config import load_config
from rmas.features.discovery import DiscoveryInput, EarlyAttention, build_discovery_features
from rmas.scoring.discovery_score import score_discovery


def test_spike_raises_mention_zscore():
    quiet = [3, 3, 4, 3, 3, 4, 3]          # baseline
    spike = quiet + [20]                    # sudden unusual discussion
    inp = DiscoveryInput(
        ticker="ABC",
        mentions_daily=spike,
        new_threads_hourly=[1, 1, 2, 1, 1, 6],
        comments_hourly=[2, 3, 2, 3, 2, 12],
        unique_authors_daily=[3, 3, 4, 3, 3, 12],
        subreddit_count=3,
    )
    feats = build_discovery_features(inp)
    assert feats["mention_z_7d"] > 0.7
    assert feats["_raw_mention_z_7d"] > 1.5


def test_flat_series_low_score():
    flat = [5, 5, 5, 5, 5, 5, 5, 5]
    inp = DiscoveryInput(
        ticker="FLAT", mentions_daily=flat,
        new_threads_hourly=[1, 1, 1, 1, 1, 1],
        comments_hourly=[2, 2, 2, 2, 2, 2],
        unique_authors_daily=[4, 4, 4, 4, 4, 4],
        subreddit_count=1,
    )
    feats = build_discovery_features(inp)
    assert feats["mention_z_7d"] < 0.6
    assert feats["subreddit_diversity"] == 0.0


def test_early_attention_rising_vs_decaying():
    assert EarlyAttention([1, 2, 3, 5]).is_rising()
    assert EarlyAttention([5, 3, 2, 1]).is_decaying()
    assert not EarlyAttention([5, 3, 2, 1]).is_rising()


def test_lead_user_boost_increases_features():
    base = DiscoveryInput("ABC", [3, 3, 4, 3, 3, 4, 3, 15],
                          [1, 1, 2, 1, 1, 6], [2, 3, 2, 3, 2, 12],
                          [3, 3, 4, 3, 3, 12], subreddit_count=3)
    boosted = DiscoveryInput("ABC", base.mentions_daily, base.new_threads_hourly,
                             base.comments_hourly, base.unique_authors_daily,
                             subreddit_count=3, lead_user_weight=1.0)
    f0 = build_discovery_features(base)
    f1 = build_discovery_features(boosted)
    assert f1["mention_z_7d"] >= f0["mention_z_7d"]


def test_apewisdom_accel_feature_squashed_and_raw_exposed():
    flat = [5, 5, 5, 5, 5, 5, 5, 5]
    inp = DiscoveryInput(
        ticker="ABC", mentions_daily=flat,
        new_threads_hourly=[1, 1, 1, 1, 1, 1],
        comments_hourly=[2, 2, 2, 2, 2, 2],
        unique_authors_daily=[4, 4, 4, 4, 4, 4],
        subreddit_count=1, apewisdom_accel=2.0,
    )
    feats = build_discovery_features(inp)
    assert feats["_raw_apewisdom_accel_z"] == 2.0
    assert 0.0 < feats["apewisdom_accel"] < 1.0        # squashed to [0,1]


# ------------------------------------------------------ discovery gate OR-logic
_CFG = load_config()


def _feats(mention_z_7d=0.5, unique_authors=12, bot_ratio=0.1, apewisdom_accel_z=0.0):
    return {
        "mention_z_7d": 0.4, "mention_z_30d": 0.4, "mention_acceleration": 0.4,
        "new_threads_per_hour_z": 0.4, "comments_per_hour_z": 0.4,
        "unique_authors_z": 0.4, "author_growth": 0.4, "subreddit_diversity": 0.4,
        "apewisdom_accel": 0.4,
        "_raw_mention_z_7d": mention_z_7d,
        "_raw_unique_authors": unique_authors,
        "_raw_bot_ratio": bot_ratio,
        "_raw_apewisdom_accel_z": apewisdom_accel_z,
    }


def test_gate_apewisdom_accel_satisfies_early_attention_when_mention_z_thin():
    # own mention_z_7d well below the gate floor, but ApeWisdom accel is
    # strong enough on its own -> early-attention requirement still passes
    feats = _feats(mention_z_7d=0.2, apewisdom_accel_z=2.0)
    g = score_discovery(feats, _CFG.discovery)
    assert not any("mention_z_7d" in b for b in g.blockers)
    assert any("apewisdom_accel_z" in r for r in g.reasons)


def test_gate_blocked_when_both_mention_z_and_apewisdom_thin():
    feats = _feats(mention_z_7d=0.2, apewisdom_accel_z=0.5)   # both below floor
    g = score_discovery(feats, _CFG.discovery)
    assert any("mention_z_7d" in b for b in g.blockers)


def test_gate_still_blocked_by_low_authors_despite_apewisdom_accel():
    feats = _feats(mention_z_7d=0.2, apewisdom_accel_z=3.0, unique_authors=2)
    g = score_discovery(feats, _CFG.discovery)
    assert not g.green
    assert any("unique_authors" in b for b in g.blockers)


def test_gate_still_blocked_by_high_bot_ratio_despite_apewisdom_accel():
    feats = _feats(mention_z_7d=0.2, apewisdom_accel_z=3.0, bot_ratio=0.9)
    g = score_discovery(feats, _CFG.discovery)
    assert not g.green
    assert any("bot_ratio" in b for b in g.blockers)
