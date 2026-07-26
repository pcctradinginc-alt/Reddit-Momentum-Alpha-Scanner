"""ApeWisdom: aggregated Reddit ticker mentions (INCLUDING comments).

Free, keyless API (https://apewisdom.io/api/). One request per scan, cached
per day — running cost zero.

Roles in the pipeline:
  1. the timing gate's ``is_top_hype_ticker`` input. A name sitting at the
     very top of the aggregated hype list is by definition *late* for an
     early-attention strategy.
  2. ``attention_accel_z`` — a persisted, cold-start-robust DISCOVERY input
     (see ``ApeWisdomStore``). ApeWisdom aggregates Reddit itself (across
     many subs, including comments our own RSS radar can't see), so this is
     a same-channel, richer view of "how much is this being discussed" —
     it only ENRICHES discovery.

DESIGN RULE: we deliberately do NOT feed ApeWisdom into the cross-source
divergence score, and ``attention_accel_z`` must never be treated as an
independent cross-source channel — ApeWisdom aggregates Reddit itself, so
treating it as one would systematically flag our own radar as "broad
euphoria". It can only ever help a ticker our own radar already saw (the
scan only calls this for tickers already in the RSS mention set) — it can
never introduce unseen tickers.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from rmas.config import Secrets, is_offline
from rmas.data import cache
from rmas.data.apewisdom_store import ApeWisdomStore
from rmas.logging_setup import get_logger
from rmas.mathx import clip, zscore

log = get_logger("data.apewisdom")

_URL = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/1"
MIN_HISTORY_DAYS = 5
Z_CLAMP = 3.0


class ApeWisdomAdapter:
    def __init__(self, secrets: Secrets | None = None, offline: bool | None = None,
                 store_path=None):
        self.secrets = secrets or Secrets()
        self.offline = is_offline(self.secrets) if offline is None else offline
        self._top: dict[str, dict] | None = None
        self._fetched = False
        # Lazy-init (like x_source): only created once a caller actually asks
        # for attention_accel_z, so tests/offline runs never touch disk.
        self._store_path = store_path
        self._store: ApeWisdomStore | None = None

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

    def mentions(self, ticker: str) -> int:
        """Today's ApeWisdom mention count. 0 when unknown."""
        entry = self.top().get(ticker.upper())
        return int(entry["mentions"]) if entry else 0

    def mentions_24h_ago(self, ticker: str) -> int:
        """ApeWisdom's own 24h-ago mention count. 0 when unknown."""
        entry = self.top().get(ticker.upper())
        return int(entry["mentions_24h_ago"]) if entry else 0

    def attention_accel_z(self, ticker: str, asof: date | None = None) -> float:
        """z-score of today's ApeWisdom mentions vs the ticker's OWN
        persisted history — a DISCOVERY-only enrichment (see module
        docstring: never an independent cross-source channel).

        Records today's mention count into a persisted ``ApeWisdomStore`` on
        first call per ticker (like x_source's same-day recording). Once the
        ticker has >=MIN_HISTORY_DAYS recorded prior days, returns a true
        z-score against that history.

        COLD-START BRIDGE: with fewer than MIN_HISTORY_DAYS of persisted
        history, falls back to a bounded delta between today's `mentions`
        and ApeWisdom's own `mentions_24h_ago` — available from day one —
        so there's a real early signal immediately instead of a dead zero
        for a week.

        Returns 0.0 when offline, the ticker is unknown to ApeWisdom, or on
        any internal failure (never raises). Clamped to +/-3.
        """
        if self.offline:
            return 0.0
        try:
            tkr = ticker.upper()
            entry = self.top().get(tkr)
            if not entry:
                return 0.0
            asof = asof or datetime.now(timezone.utc).date()
            if self._store is None:
                self._store = ApeWisdomStore(path=self._store_path)

            cur = float(entry.get("mentions") or 0)
            self._store.record(asof, {tkr: cur})
            self._store.save()

            if self._store.observed_days(tkr, asof) >= MIN_HISTORY_DAYS:
                series = self._store.series(tkr, asof, days=30)
                z = zscore(series[-1], series[:-1])
            else:
                prev = float(entry.get("mentions_24h_ago") or 0)
                z = (cur / max(1.0, prev)) - 1.0
            return round(clip(z, -Z_CLAMP, Z_CLAMP), 3)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("apewisdom attention_accel_z failed for %s (%s)", ticker, exc)
            return 0.0

    def growth(self, ticker: str) -> float:
        """[0,1] mention-growth score vs 24h ago — INCLUDES comments, which
        the RSS radar can't see. 0 when unknown; 1.0 at 3x growth. Bounded
        and additive-only by the callers' contract."""
        entry = self.top().get(ticker.upper())
        if not entry:
            return 0.0
        prev = entry.get("mentions_24h_ago") or 0
        cur = entry.get("mentions") or 0
        if prev <= 0 or cur <= 0:
            return 0.0
        ratio = cur / prev
        return round(min(1.0, max(0.0, (ratio - 1.0) / 2.0)), 4)
