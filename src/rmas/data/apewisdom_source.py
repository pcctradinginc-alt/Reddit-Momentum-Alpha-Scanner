"""ApeWisdom: aggregated Reddit ticker mentions (INCLUDING comments).

Free, keyless API (https://apewisdom.io/api/). One request per scan, cached
per day — running cost zero.

Role in the pipeline: the timing gate's ``is_top_hype_ticker`` input. A name
sitting at the very top of the aggregated hype list is by definition *late*
for an early-attention strategy. We deliberately do NOT feed this into the
cross-source divergence score: ApeWisdom aggregates Reddit itself, so it is
not an independent channel — treating it as one would systematically flag our
own radar as "broad euphoria".
"""

from __future__ import annotations

from datetime import datetime, timezone

from rmas.config import Secrets, is_offline
from rmas.data import cache
from rmas.logging_setup import get_logger

log = get_logger("data.apewisdom")

_URL = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/1"


class ApeWisdomAdapter:
    def __init__(self, secrets: Secrets | None = None, offline: bool | None = None):
        self.secrets = secrets or Secrets()
        self.offline = is_offline(self.secrets) if offline is None else offline
        self._top: dict[str, dict] | None = None
        self._fetched = False

    def top(self) -> dict[str, dict]:
        """{ticker: {rank, mentions, mentions_24h_ago, upvotes}} — top ~100.

        Empty dict offline or on failure (callers treat missing as
        "not a top-hype ticker", the conservative default).
        """
        if self._fetched:
            return self._top or {}
        self._fetched = True
        if self.offline:
            self._top = {}
            return {}

        day = datetime.now(timezone.utc).date().isoformat()
        cached = cache.get("apewisdom", day)
        if cached is not None:
            self._top = cached
            return cached

        try:  # pragma: no cover - live network
            import requests

            r = requests.get(_URL, timeout=15)
            r.raise_for_status()
            results = r.json().get("results") or []
            self._top = {
                str(it.get("ticker", "")).upper(): {
                    "rank": int(it.get("rank", 0)),
                    "mentions": int(it.get("mentions") or 0),
                    "mentions_24h_ago": int(it.get("mentions_24h_ago") or 0),
                    "upvotes": int(it.get("upvotes") or 0),
                }
                for it in results if it.get("ticker")
            }
            cache.put("apewisdom", day, self._top)
            log.info("apewisdom: %d tickers in today's hype list", len(self._top))
            return self._top
        except Exception as exc:  # pragma: no cover
            log.warning("apewisdom fetch failed (%s)", exc)
            self._top = {}
            return {}

    def rank(self, ticker: str) -> int | None:
        entry = self.top().get(ticker.upper())
        return entry["rank"] if entry else None
