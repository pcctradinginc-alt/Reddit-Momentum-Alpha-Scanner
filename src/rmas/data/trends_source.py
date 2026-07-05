"""Cross-source attention: Google Trends (pytrends) and optional X/Twitter.

Returns z-scored attention for a ticker. Used to detect whether Reddit is
*leading* (good, early divergence) or whether everyone is already euphoric
(late). Synthetic fallback offline / without pytrends.
"""

from __future__ import annotations

from rmas.config import Secrets, is_offline
from rmas.data.base import _seed_for
from rmas.logging_setup import get_logger
from rmas.mathx import zscore

log = get_logger("data.trends")


class TrendsAdapter:
    def __init__(self, secrets: Secrets | None = None, offline: bool | None = None):
        self.secrets = secrets or Secrets()
        self.offline = is_offline(self.secrets) if offline is None else offline

    def google_trends_z(self, ticker: str, company_name: str | None = None) -> float:
        if self.offline:
            import random

            rng = random.Random(_seed_for(ticker, "gtrends"))
            # Offline: mostly quiet (so reddit can "lead"); occasional spike.
            return round(rng.gauss(0.2, 0.6), 3)
        try:  # pragma: no cover - live network
            from pytrends.request import TrendReq

            pt = TrendReq(hl="en-US", tz=360)
            kw = company_name or ticker
            pt.build_payload([kw], timeframe="now 7-d")
            df = pt.interest_over_time()
            if df.empty:
                return 0.0
            series = df[kw].tolist()
            return round(zscore(series[-1], series[:-1]), 3)
        except Exception as exc:  # pragma: no cover
            log.warning("google trends failed for %s (%s)", ticker, exc)
            return 0.0

    def x_attention_z(self, ticker: str) -> float:
        """X/Twitter attention. No implementation yet — LIVE runs must return
        a neutral 0.0 (never fabricated noise into a real ranking). The
        synthetic value exists only for offline demos/tests."""
        if not self.offline:
            return 0.0
        import random

        rng = random.Random(_seed_for(ticker, "x"))
        return round(rng.gauss(0.1, 0.5), 3)
