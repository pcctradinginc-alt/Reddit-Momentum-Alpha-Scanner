"""X (Twitter) attention cross-check — WITHOUT any X API access.

Reality check (tested 2026-07-05): every keyless path into X itself is dead —
all public Nitter instances return 403, xcancel's RSS needs a manual e-mail
whitelist, and X's own search is login-walled. Building on those would break
weekly. The stable alternative is **StockTwits**: the finance-native X clone
(cashtag streams, same retail crowd, heavy user overlap), whose public JSON
API has been keyless and stable for years.

This adapter is therefore source-agnostic "X-style attention": message volume,
unique posters, crowd sentiment and WATCHLIST COUNT per ticker. A real X feed
can be plugged in behind the same interface later.

Alpha-safety contract (the reason for the design):
  * every failure / missing datum degrades to NEUTRAL (z=0, growth=0) — the
    scoring then behaves exactly as before this feature existed;
  * attention_z needs >=5 recorded days of the ticker's own history before it
    is non-zero (same honest cold start as the Reddit attention store);
  * signals are bounded (z clamped to +-3, growth clipped to [0,1]) so one
    bad scrape can never dominate a rank.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

from rmas.config import ROOT, Secrets, is_offline
from rmas.data.attention_store import AttentionStore
from rmas.data.base import _seed_for
from rmas.logging_setup import get_logger
from rmas.mathx import clip, zscore

log = get_logger("data.x")

_STREAM_URL = "https://api.stocktwits.com/api/2/streams/symbol/{sym}.json"
# StockTwits 403s the default python-requests UA; a browser UA is served.
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36"}
X_HISTORY_PATH = ROOT / "data" / "state" / "x_history.json"       # [msgs, users]
X_WATCHERS_PATH = ROOT / "data" / "state" / "x_watchers.json"     # [watchers, 0]
MAX_PAGES = 3               # 30 msgs/page; >=90 msgs/24h recorded as capped 90
MIN_WATCHER_DAYS = 3
Z_CLAMP = 3.0


def _metrics_from_messages(pages: list[dict], now: datetime) -> dict:
    """Pure aggregation over stream pages (testable without network)."""
    cutoff = now - timedelta(hours=24)
    msgs = 0
    users: set[str] = set()
    bullish = bearish = 0
    watchers = 0.0
    for page in pages:
        sym = page.get("symbol") or {}
        watchers = max(watchers, float(sym.get("watchlist_count") or 0))
        for m in page.get("messages", []):
            try:
                ts = datetime.fromisoformat(
                    str(m.get("created_at", "")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts < cutoff:
                continue
            msgs += 1
            users.add(str((m.get("user") or {}).get("id", "")))
            senti = (((m.get("entities") or {}).get("sentiment")) or {}).get("basic")
            if senti == "Bullish":
                bullish += 1
            elif senti == "Bearish":
                bearish += 1
    rated = bullish + bearish
    return {
        "msgs_24h": float(msgs),
        "users_24h": float(len(users)),
        "watchers": watchers,
        "bullish_pct": round(bullish / rated * 100.0, 1) if rated else None,
    }


class XAttentionAdapter:
    def __init__(self, secrets: Secrets | None = None, offline: bool | None = None,
                 history_path=None, watchers_path=None):
        self.secrets = secrets or Secrets()
        self.offline = is_offline(self.secrets) if offline is None else offline
        self._history = AttentionStore(path=history_path or X_HISTORY_PATH)
        self._watchers = AttentionStore(path=watchers_path or X_WATCHERS_PATH)
        self._memo: dict[str, dict | None] = {}
        # Circuit breaker: after 3 consecutive failures the source is
        # considered down for the rest of the run — 25 tickers x 8s timeouts
        # must never stall the scan. Everything degrades to neutral.
        self._consecutive_failures = 0
        self._dead = False

    # ------------------------------------------------------------------ #
    def _fetch_stream(self, ticker: str) -> list[dict] | None:
        """Up to MAX_PAGES of the public symbol stream. None on any failure."""
        try:  # pragma: no cover - live network
            import requests

            pages: list[dict] = []
            max_id: int | None = None
            for _ in range(MAX_PAGES):
                params = {"max": max_id} if max_id else {}
                r = requests.get(_STREAM_URL.format(sym=ticker),
                                 params=params, headers=_UA, timeout=8)
                if r.status_code == 429:
                    time.sleep(2)
                    r = requests.get(_STREAM_URL.format(sym=ticker),
                                     params=params, headers=_UA, timeout=8)
                if r.status_code != 200:
                    break
                page = r.json()
                pages.append(page)
                cursor = page.get("cursor") or {}
                msgs = page.get("messages") or []
                if not cursor.get("more") or not msgs:
                    break
                oldest = msgs[-1].get("created_at", "")
                ts = datetime.fromisoformat(str(oldest).replace("Z", "+00:00"))
                if ts < datetime.now(timezone.utc) - timedelta(hours=24):
                    break
                max_id = cursor.get("max")
            return pages or None
        except Exception as exc:  # pragma: no cover
            log.warning("stocktwits stream failed for %s (%s)", ticker, exc)
            return None

    # ------------------------------------------------------------------ #
    def metrics(self, ticker: str, asof: date | None = None) -> dict | None:
        """Today's X-style attention metrics; records history. None = unknown."""
        ticker = ticker.upper()
        if ticker in self._memo:
            return self._memo[ticker]
        if self.offline or self._dead:
            self._memo[ticker] = None
            return None
        asof = asof or datetime.now(timezone.utc).date()
        time.sleep(0.5)                     # politeness pacing between tickers
        pages = self._fetch_stream(ticker)
        if pages is None:
            self._memo[ticker] = None
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                self._dead = True
                log.warning("x/stocktwits source down after %d consecutive "
                            "failures — neutral for the rest of this run",
                            self._consecutive_failures)
            return None
        self._consecutive_failures = 0
        m = _metrics_from_messages(pages, datetime.now(timezone.utc))
        self._history.record(asof, {ticker: int(m["msgs_24h"])},
                             {ticker: int(m["users_24h"])})
        self._history.save()
        self._watchers.record(asof, {ticker: int(m["watchers"])})
        self._watchers.save()
        self._memo[ticker] = m
        return m

    # ------------------------------------------------------------------ #
    def attention_z(self, ticker: str, asof: date | None = None) -> float:
        """z-score of today's message volume vs the ticker's OWN history.

        Neutral 0.0 while unknown/cold. Offline: the deterministic synthetic
        demo value (bit-identical to the pre-X-integration pipeline)."""
        if self.offline:
            import random

            rng = random.Random(_seed_for(ticker, "x"))
            return round(rng.gauss(0.1, 0.5), 3)
        asof = asof or datetime.now(timezone.utc).date()
        if self.metrics(ticker, asof) is None:
            return 0.0
        series = self._history.series(ticker.upper(), asof, days=15)
        z = zscore(series[-1], series[:-1])
        return round(max(-Z_CLAMP, min(Z_CLAMP, z)), 3)

    def watchers_growth(self, ticker: str, asof: date | None = None) -> float:
        """[0,1] score for watchlist-count inflow — retail attention arriving.

        Watchlist adds are hard to bot (an account watches a symbol once), so
        growth here is a conservative CONFIRMATION signal. 0.0 while unknown,
        offline, or without MIN_WATCHER_DAYS of history — additive-only by
        contract, it can never reduce a rank."""
        if self.offline:
            return 0.0
        asof = asof or datetime.now(timezone.utc).date()
        if self.metrics(ticker, asof) is None:
            return 0.0
        ticker = ticker.upper()
        if self._watchers.observed_days(ticker, asof) < MIN_WATCHER_DAYS:
            return 0.0
        series = self._watchers.series(ticker, asof, days=MIN_WATCHER_DAYS + 4)
        today, prior = series[-1], series[:-1]
        base = sorted(prior)[len(prior) // 2]     # median of prior days
        if base <= 0:
            return 0.0
        growth = (today - base) / base
        return round(clip(growth * 10.0), 4)      # +10%/day watchers -> 1.0
