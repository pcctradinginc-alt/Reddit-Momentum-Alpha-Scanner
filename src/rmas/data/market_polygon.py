"""Polygon.io market-data adapter (fallback / cross-check for bars).

Same Bar interface as Alpaca so the pipeline can use either. Synthetic fallback
offline / without a key.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rmas.config import Secrets, is_offline
from rmas.data.base import SyntheticMarket
from rmas.logging_setup import get_logger
from rmas.types import Bar, LiquiditySnapshot

log = get_logger("data.polygon")


class PolygonAdapter:
    def __init__(self, secrets: Secrets | None = None, offline: bool | None = None):
        self.secrets = secrets or Secrets()
        self.offline = is_offline(self.secrets) if offline is None else offline
        self._synthetic = SyntheticMarket()

    @property
    def _live(self) -> bool:
        return not self.offline and self.secrets.has("POLYGON_API_KEY")

    def daily_bars(self, ticker: str, lookback_days: int = 60) -> list[Bar]:
        if not self._live:
            return self._synthetic.daily_bars(ticker, lookback_days)
        try:  # pragma: no cover - live network
            import requests

            key = self.secrets.get("POLYGON_API_KEY")
            end = datetime.now(timezone.utc).date()
            start = end - timedelta(days=lookback_days * 2)
            url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/"
                   f"{start.isoformat()}/{end.isoformat()}")
            r = requests.get(url, params={"adjusted": "true", "sort": "asc",
                                          "limit": 50000, "apiKey": key}, timeout=15)
            r.raise_for_status()
            results = r.json().get("results", [])
            out = [
                Bar(
                    t=datetime.fromtimestamp(b["t"] / 1000, tz=timezone.utc),
                    open=b["o"], high=b["h"], low=b["l"], close=b["c"], volume=b["v"],
                )
                for b in results
            ][-lookback_days:]
            return out or self._synthetic.daily_bars(ticker, lookback_days)
        except Exception as exc:  # pragma: no cover
            log.warning("polygon bars failed for %s (%s); synthetic", ticker, exc)
            return self._synthetic.daily_bars(ticker, lookback_days)

    def liquidity(self, ticker: str) -> LiquiditySnapshot:
        return self._synthetic.liquidity(ticker)
