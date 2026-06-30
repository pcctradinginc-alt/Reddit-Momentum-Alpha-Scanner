"""News / catalyst headlines via Finnhub or Polygon, with synthetic fallback.

Returns a flat list of recent headline strings per ticker; the catalyst module
classifies them. Used by cross-source (news_count_z) and catalyst detection.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rmas.config import Secrets, is_offline
from rmas.data.base import _seed_for
from rmas.logging_setup import get_logger

log = get_logger("data.news")

_SYNTH_TEMPLATES = [
    "{t} announces new product line",
    "Analysts weigh in on {t} after recent move",
    "{t} short interest rises",
]


class NewsAdapter:
    def __init__(self, secrets: Secrets | None = None, offline: bool | None = None):
        self.secrets = secrets or Secrets()
        self.offline = is_offline(self.secrets) if offline is None else offline

    def headlines(self, ticker: str, lookback_days: int = 5) -> list[str]:
        if self.offline or not self.secrets.has("FINNHUB_API_KEY"):
            # deterministic, often-empty synthetic news (most names have none)
            import random

            rng = random.Random(_seed_for(ticker, "news"))
            if rng.random() > 0.6:
                return [t.format(t=ticker) for t in _SYNTH_TEMPLATES[: rng.randint(1, 3)]]
            return []
        try:  # pragma: no cover - live network
            import requests

            key = self.secrets.get("FINNHUB_API_KEY")
            end = datetime.now(timezone.utc).date()
            start = end - timedelta(days=lookback_days)
            r = requests.get(
                "https://finnhub.io/api/v1/company-news",
                params={"symbol": ticker, "from": start.isoformat(),
                        "to": end.isoformat(), "token": key},
                timeout=15,
            )
            r.raise_for_status()
            return [item.get("headline", "") for item in r.json()][:25]
        except Exception as exc:  # pragma: no cover
            log.warning("news fetch failed for %s (%s)", ticker, exc)
            return []
