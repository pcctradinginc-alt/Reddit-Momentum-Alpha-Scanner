"""Bot / shill / spam / coordinated-pump detection.

Goal: down-weight astroturf so the Discovery gate measures *organic* attention.
We compute a per-author/per-mention bot probability from cheap, robust signals:

  * account age (very new accounts are suspicious)
  * posting frequency (burst posting)
  * text similarity / duplication (copy-paste shilling)
  * single-poster tickers (one account = the whole "hype")
  * link spam and emoji/meme overload
  * coordinated repetition (same text from many accounts in a short window)

Everything is heuristic and bounded to [0, 1]; weights are deliberately gentle
so genuine retail enthusiasm isn't nuked, only obvious manipulation.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass

from rmas.types import Mention

_URL = re.compile(r"https?://\S+")
_EMOJI = re.compile(
    "[" "\U0001f300-\U0001faff" "\U00002600-\U000027bf" "\U0001f1e6-\U0001f1ff" "]",
    flags=re.UNICODE,
)
_WORD = re.compile(r"[a-z0-9']+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def emoji_ratio(text: str) -> float:
    if not text:
        return 0.0
    n = len(_EMOJI.findall(text))
    return min(1.0, n / max(8, len(text) / 6))


def link_spam_score(text: str) -> float:
    return min(1.0, len(_URL.findall(text)) / 3.0)


@dataclass
class BotConfig:
    min_account_age_days: float = 30.0
    burst_posts_threshold: int = 8       # posts by same author in the batch
    dup_similarity: float = 0.85         # jaccard above this == duplicate
    weights: dict[str, float] | None = None

    def w(self, key: str, default: float) -> float:
        return (self.weights or {}).get(key, default)


def score_mentions(mentions: list[Mention], cfg: BotConfig | None = None) -> list[Mention]:
    """Annotate each mention with ``bot_probability`` in-place and return the list."""
    cfg = cfg or BotConfig()
    if not mentions:
        return mentions

    # --- precompute per-author stats & duplicate clusters ---
    by_author: dict[str, list[Mention]] = defaultdict(list)
    for m in mentions:
        by_author[m.author].append(m)

    token_sets = [_tokens(m.text) for m in mentions]

    # coordinated repetition: how many *other* mentions are near-duplicates
    dup_counts = [0] * len(mentions)
    for i in range(len(mentions)):
        for j in range(i + 1, len(mentions)):
            if jaccard(token_sets[i], token_sets[j]) >= cfg.dup_similarity:
                dup_counts[i] += 1
                dup_counts[j] += 1

    # tickers pushed by a single author only -> shill risk
    ticker_authors: dict[str, set[str]] = defaultdict(set)
    for m in mentions:
        ticker_authors[m.ticker].add(m.author)

    for idx, m in enumerate(mentions):
        signals: list[tuple[float, float]] = []  # (signal_value, weight)

        # account age
        if m.author_age_days is not None:
            age_sig = max(0.0, 1.0 - m.author_age_days / cfg.min_account_age_days)
            signals.append((age_sig, cfg.w("age", 0.25)))

        # burst posting by this author
        n_posts = len(by_author[m.author])
        burst_sig = min(1.0, max(0.0, (n_posts - 1) / cfg.burst_posts_threshold))
        signals.append((burst_sig, cfg.w("burst", 0.15)))

        # duplicate / coordinated text
        dup_sig = 1.0 - math.exp(-dup_counts[idx] / 2.0)
        signals.append((dup_sig, cfg.w("dup", 0.25)))

        # single-poster ticker
        single_sig = 1.0 if len(ticker_authors[m.ticker]) <= 1 and n_posts >= 2 else 0.0
        signals.append((single_sig, cfg.w("single", 0.10)))

        # link spam
        signals.append((link_spam_score(m.text), cfg.w("links", 0.10)))

        # emoji / meme overload
        signals.append((emoji_ratio(m.text), cfg.w("emoji", 0.15)))

        total_w = sum(w for _, w in signals) or 1.0
        prob = sum(s * w for s, w in signals) / total_w

        # Coordinated repetition is near-certain manipulation regardless of how
        # "clean" the individual account looks (old account, no emoji, etc.), so
        # a strong duplicate cluster overrides the gentle weighted average.
        if dup_counts[idx] >= 4:
            prob = max(prob, 0.75)

        m.bot_probability = round(min(1.0, max(0.0, prob)), 4)

    return mentions


def bot_ratio(mentions: list[Mention], threshold: float = 0.6) -> float:
    """Fraction of mentions whose bot_probability exceeds ``threshold``."""
    if not mentions:
        return 0.0
    flagged = sum(1 for m in mentions if m.bot_probability >= threshold)
    return flagged / len(mentions)
