"""Finnhub fundamentals: real market cap for the liquidity hard-filter.

Free tier (60 calls/min) is far above our capped per-scan symbol count, and
responses are cached per day, so the marginal cost of this adapter is zero.
Returns None without a key / on failure — callers fall back to synthetic.
"""

from __future__ import annotations

from datetime import datetime, timezone

from rmas.config import Secrets, is_offline
from rmas.data import cache
from rmas.logging_setup import get_logger

log = get_logger("data.finnhub")


class FinnhubFundamentals:
    def __init__(self, secrets: Secrets | None = None, offline: bool | None = None):
        self.secrets = secrets or Secrets()
        self.offline = is_offline(self.secrets) if offline is None else offline

    @property
    def _live(self) -> bool:
        return not self.offline and self.secrets.has("FINNHUB_API_KEY")

    def profile(self, ticker: str) -> dict | None:
        """Company profile (/stock/profile2, free tier), cached per day.

        One request serves market cap, shares outstanding AND industry."""
        if not self._live:
            return None
        day = datetime.now(timezone.utc).date().isoformat()
        cached = cache.get("finnhub_profile2", f"{ticker}_{day}")
        if cached is not None:
            return cached or None
        try:  # pragma: no cover - live network
            import requests

            r = requests.get(
                "https://finnhub.io/api/v1/stock/profile2",
                params={"symbol": ticker, "token": self.secrets.get("FINNHUB_API_KEY")},
                timeout=15,
            )
            r.raise_for_status()
            p = r.json() or {}
            cache.put("finnhub_profile2", f"{ticker}_{day}", p)
            return p or None
        except Exception as exc:  # pragma: no cover
            log.warning("finnhub profile failed for %s (%s)", ticker, exc)
            return None

    def market_cap_usd(self, ticker: str) -> float | None:
        p = self.profile(ticker)
        if not p:
            return None
        try:
            cap = float(p.get("marketCapitalization") or 0.0) * 1_000_000.0
        except (TypeError, ValueError):
            return None
        return cap or None

    def shares_outstanding(self, ticker: str) -> float | None:
        """Shares outstanding (an upper bound for float — Finnhub free has no
        true free-float; callers must treat it as such)."""
        p = self.profile(ticker)
        if not p:
            return None
        try:
            shares = float(p.get("shareOutstanding") or 0.0) * 1_000_000.0
        except (TypeError, ValueError):
            return None
        return shares or None

    def industry(self, ticker: str) -> str | None:
        p = self.profile(ticker)
        return (p or {}).get("finnhubIndustry") or None

    # ------------------------------------------------------------------ #
    def metrics(self, ticker: str) -> dict | None:
        """Basic financials (/stock/metric, free tier), cached per day."""
        if not self._live:
            return None
        day = datetime.now(timezone.utc).date().isoformat()
        cached = cache.get("finnhub_metric", f"{ticker}_{day}")
        if cached is not None:
            return cached or None
        try:  # pragma: no cover - live network
            import requests

            r = requests.get(
                "https://finnhub.io/api/v1/stock/metric",
                params={"symbol": ticker, "metric": "all",
                        "token": self.secrets.get("FINNHUB_API_KEY")},
                timeout=15,
            )
            r.raise_for_status()
            m = r.json().get("metric") or {}
            cache.put("finnhub_metric", f"{ticker}_{day}", m)
            return m or None
        except Exception as exc:  # pragma: no cover
            log.warning("finnhub metric failed for %s (%s)", ticker, exc)
            return None

    def avg_dollar_volume_usd(self, ticker: str, close: float) -> float | None:
        """Consolidated 10d avg dollar volume — immune to the IEX-sliver
        problem of free Alpaca bars. Finnhub reports volume in MILLIONS of
        shares."""
        m = self.metrics(ticker)
        if not m or close <= 0:
            return None
        vol_millions = m.get("10DayAverageTradingVolume") or m.get(
            "3MonthAverageTradingVolume") or 0.0
        try:
            dollar = float(vol_millions) * 1_000_000.0 * close
        except (TypeError, ValueError):
            return None
        return dollar or None
