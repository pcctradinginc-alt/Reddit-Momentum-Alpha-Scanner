"""News / catalyst headlines, with several live sources and synthetic fallback.

Source priority (first one that's configured wins):
  1. Finnhub  (FINNHUB_API_KEY)
  2. Alpaca News API (ALPACA_API_KEY/SECRET) — included with Alpaca market data
  3. synthetic (offline / no keys)

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
        if not self.offline:
            if self.secrets.has("FINNHUB_API_KEY"):
                hl = self._finnhub(ticker, lookback_days)
                if hl is not None:
                    return hl
            if self.secrets.alpaca_ready:
                hl = self._alpaca(ticker, lookback_days)
                if hl is not None:
                    return hl
        return self._synthetic(ticker)

    # ------------------------------------------------------------------ #
    def _synthetic(self, ticker: str) -> list[str]:
        import random

        rng = random.Random(_seed_for(ticker, "news"))
        if rng.random() > 0.6:
            return [t.format(t=ticker) for t in _SYNTH_TEMPLATES[: rng.randint(1, 3)]]
        return []

    def _finnhub(self, ticker: str, lookback_days: int) -> list[str] | None:
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
            log.warning("finnhub news failed for %s (%s)", ticker, exc)
            return None

    def _alpaca(self, ticker: str, lookback_days: int) -> list[str] | None:
        """Alpaca News API — uses the same Alpaca keys as market data."""
        try:  # pragma: no cover - live network
            import requests

            data_url = self.secrets.get("ALPACA_DATA_URL", "https://data.alpaca.markets")
            start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
            r = requests.get(
                f"{data_url}/v1beta1/news",
                headers={
                    "APCA-API-KEY-ID": self.secrets.get("ALPACA_API_KEY"),
                    "APCA-API-SECRET-KEY": self.secrets.get("ALPACA_API_SECRET"),
                },
                params={"symbols": ticker, "start": start, "limit": 25,
                        "sort": "desc"},
                timeout=15,
            )
            r.raise_for_status()
            items = r.json().get("news", [])
            return [it.get("headline", "") for it in items if it.get("headline")][:25]
        except Exception as exc:  # pragma: no cover
            log.warning("alpaca news failed for %s (%s)", ticker, exc)
            return None
