"""Discovery features — measure *early attention acceleration*, not raw volume.

Reddit is a radar: we want a previously-quiet ticker that suddenly gets
unusually discussed, ideally *before* mainstream/Google/X catch up. So the
features emphasize change (z-scores, acceleration, author growth) over level.

All outputs are squashed to [0,1] so they can be combined with config weights.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from rmas.mathx import clip, squash, zscore


@dataclass
class DiscoveryInput:
    """Point-in-time attention series for one ticker.

    Each list is a daily (or hourly) history ending at the observation time.
    The LAST element is "today"/current; the rest are the baseline.
    """

    ticker: str
    mentions_daily: Sequence[float]          # mentions per day, len >= 8 ideally
    new_threads_hourly: Sequence[float]      # last N hours
    comments_hourly: Sequence[float]
    unique_authors_daily: Sequence[float]
    subreddit_count: int = 1                 # distinct subreddits today
    bot_ratio: float = 0.0
    lead_user_weight: float = 0.0            # 0..1 boost from high-quality posters
    # ApeWisdom's own z-score of mention acceleration (see apewisdom_source.py
    # attention_accel_z) — same-channel enrichment, richer than our RSS radar
    # alone (includes comments, aggregates many subs). 0.0 when unknown.
    apewisdom_accel: float = 0.0


def _split(series: Sequence[float]) -> tuple[float, list[float]]:
    """Return (current, history) where current is the last point."""
    s = list(series)
    if not s:
        return 0.0, []
    return s[-1], s[:-1]


def build_discovery_features(inp: DiscoveryInput, short_days: int = 7, long_days: int = 30) -> dict[str, float]:
    md = list(inp.mentions_daily)
    cur, hist = _split(md)

    hist7 = hist[-short_days:] if hist else []
    hist30 = hist[-long_days:] if hist else []

    z7 = zscore(cur, hist7)
    z30 = zscore(cur, hist30)

    # acceleration: today's change vs the prior change (2nd difference, normalized)
    accel = 0.0
    if len(md) >= 3:
        d1 = md[-1] - md[-2]
        d0 = md[-2] - md[-3]
        base = max(1.0, abs(d0))
        accel = (d1 - d0) / base

    nt_cur, nt_hist = _split(inp.new_threads_hourly)
    cm_cur, cm_hist = _split(inp.comments_hourly)
    ua_cur, ua_hist = _split(inp.unique_authors_daily)

    z_threads = zscore(nt_cur, nt_hist)
    z_comments = zscore(cm_cur, cm_hist)
    z_authors = zscore(ua_cur, ua_hist)

    # author growth: today's unique authors vs the recent average
    ua_avg = sum(ua_hist[-short_days:]) / len(ua_hist[-short_days:]) if ua_hist else 0.0
    author_growth = (ua_cur - ua_avg) / max(1.0, ua_avg)

    diversity = clip((inp.subreddit_count - 1) / 4.0)  # 1 sub -> 0, 5+ subs -> 1

    feats = {
        "mention_z_7d": squash(z7, scale=2.0),
        "mention_z_30d": squash(z30, scale=2.0),
        "mention_acceleration": squash(accel, scale=1.5),
        "new_threads_per_hour_z": squash(z_threads, scale=2.0),
        "comments_per_hour_z": squash(z_comments, scale=2.0),
        "unique_authors_z": squash(z_authors, scale=2.0),
        "author_growth": squash(author_growth, scale=1.0),
        "subreddit_diversity": diversity,
        "apewisdom_accel": squash(inp.apewisdom_accel, scale=2.0),
    }
    # lead-user weighting nudges the strongest attention signals upward.
    if inp.lead_user_weight:
        boost = 1.0 + 0.25 * clip(inp.lead_user_weight)
        for k in ("mention_z_7d", "mention_acceleration", "unique_authors_z"):
            feats[k] = clip(feats[k] * boost)

    # expose raw values too (gates inspect these directly)
    feats["_raw_mention_z_7d"] = z7
    feats["_raw_unique_authors"] = ua_cur
    feats["_raw_bot_ratio"] = inp.bot_ratio
    feats["_raw_apewisdom_accel_z"] = inp.apewisdom_accel
    return feats


@dataclass
class EarlyAttention:
    """Helper to judge whether attention is *early* (rising, not yet decaying)."""

    series: Sequence[float] = field(default_factory=list)

    def is_rising(self) -> bool:
        s = list(self.series)
        if len(s) < 3:
            return False
        return s[-1] >= s[-2] >= s[-3] and s[-1] > s[0]

    def is_decaying(self) -> bool:
        s = list(self.series)
        if len(s) < 3:
            return False
        return s[-1] < s[-2] < s[-3]
