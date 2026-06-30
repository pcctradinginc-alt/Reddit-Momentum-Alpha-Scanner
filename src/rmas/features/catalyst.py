"""Catalyst detection — separate a *real* catalyst from pure meme hype.

A genuine catalyst (earnings beat, guidance, FDA, SEC filing, short report,
M&A, AI/product news) gives attention a fundamental reason to persist. Pure
meme hype with no catalyst decays fast. We classify and weight accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

CATALYST_KEYWORDS = {
    "earnings": ["earnings", "eps", "beat", "guidance", "revenue", "raised guidance"],
    "fda": ["fda", "approval", "phase 3", "trial", "clinical"],
    "sec_filing": ["8-k", "10-q", "13d", "13g", "s-1", "filing", "sec filing"],
    "short_report": ["short report", "hindenburg", "muddy waters", "citron"],
    "mna": ["acquisition", "merger", "buyout", "takeover", "m&a", "acquire"],
    "ai_product": ["ai", "chip", "gpu", "launch", "partnership", "contract", "product"],
}


@dataclass
class CatalystResult:
    has_catalyst: bool
    categories: list[str] = field(default_factory=list)
    score: float = 0.0                  # 0..1 strength
    is_meme_only: bool = False
    asof: datetime | None = None


def detect_catalyst(texts: list[str], headlines: list[str] | None = None) -> CatalystResult:
    """Scan reddit texts + (optional) news headlines for catalyst evidence."""
    blob = " \n ".join((texts or []) + (headlines or [])).lower()
    hits: list[str] = []
    for cat, kws in CATALYST_KEYWORDS.items():
        if any(kw in blob for kw in kws):
            hits.append(cat)

    # News headlines carry more weight than reddit chatter.
    news_weight = 0.6 if headlines else 0.0
    base = min(1.0, 0.2 * len(hits)) * (1.0 - news_weight) + news_weight * (1.0 if hits else 0.0)
    has = len(hits) > 0
    meme_only = not has  # attention with no catalyst keyword == meme-only risk
    return CatalystResult(
        has_catalyst=has,
        categories=hits,
        score=round(base, 4),
        is_meme_only=meme_only,
    )
