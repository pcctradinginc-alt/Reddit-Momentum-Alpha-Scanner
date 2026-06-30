"""Cross-source attention.

We want Reddit to lead. If Reddit is lighting up while Google Trends, X, and
news are still quiet, that's an *early divergence* — the good case. If everyone
is screaming at once (broad euphoria), we're late — down-weight it.
"""

from __future__ import annotations

from dataclasses import dataclass

from rmas.mathx import clip


@dataclass
class CrossSourceInput:
    reddit_attention_z: float          # how unusual reddit attention is (z)
    google_trends_z: float = 0.0
    x_attention_z: float = 0.0
    news_count_z: float = 0.0


def cross_source_score(inp: CrossSourceInput, prefer_divergence: bool = True,
                       penalize_broad: bool = True) -> dict[str, float]:
    """Return a [0,1] earliness score: high = reddit-early, others-quiet."""
    others = [inp.google_trends_z, inp.x_attention_z, inp.news_count_z]
    others_max = max(others) if others else 0.0

    # Divergence: reddit hot, others cold.
    divergence = inp.reddit_attention_z - others_max
    earliness = clip(0.5 + 0.25 * divergence) if prefer_divergence else 0.5

    # Broad euphoria penalty: everyone elevated simultaneously.
    broad = min(inp.reddit_attention_z, inp.google_trends_z, inp.x_attention_z, inp.news_count_z)
    if penalize_broad and broad >= 1.5:
        earliness *= 0.6

    return {
        "cross_source_earliness": clip(earliness),
        "_raw_divergence": divergence,
        "_raw_others_max_z": others_max,
    }
