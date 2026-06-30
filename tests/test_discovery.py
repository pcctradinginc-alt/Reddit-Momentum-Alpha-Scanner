from rmas.features.discovery import DiscoveryInput, EarlyAttention, build_discovery_features


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
