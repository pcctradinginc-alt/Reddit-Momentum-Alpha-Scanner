"""Persistent per-ticker daily ATM-IV observations -> a REAL IV rank.

IV rank = where today's IV sits inside the ticker's own recent IV range.
No free API provides IV history, so we build it ourselves: every scan
records the chain's median mid-IV; once enough observations exist the
percentile is genuine. Until then callers get None and must stay NEUTRAL —
the previous implementation returned the IV *level* as "rank", which pinned
high-vol names at 100 and silently tripped the max_iv_rank gate.

Lives in data/state/ next to the attention history (same actions/cache
persistence in CI).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from rmas.config import ROOT
from rmas.logging_setup import get_logger

log = get_logger("data.iv_store")

STATE_PATH = ROOT / "data" / "state" / "iv_history.json"
MIN_OBSERVATIONS = 20
KEEP_DAYS = 400


class IVStore:
    """{ticker: {"YYYY-MM-DD": median_chain_iv}}"""

    def __init__(self, path: Path | None = None):
        self.path = path or STATE_PATH
        self._data: dict[str, dict[str, float]] = {}
        try:
            if self.path.exists():
                self._data = json.loads(self.path.read_text())
        except Exception as exc:
            log.warning("iv store unreadable (%s); starting fresh", exc)
            self._data = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, sort_keys=True))
        except Exception as exc:
            log.warning("iv store save failed (%s)", exc)

    def record(self, asof: date, ticker: str, iv: float) -> None:
        if iv <= 0:
            return
        days = self._data.setdefault(ticker, {})
        days[asof.isoformat()] = round(float(iv), 4)
        cutoff = (asof - timedelta(days=KEEP_DAYS)).isoformat()
        self._data[ticker] = {d: v for d, v in days.items() if d >= cutoff}

    def rank(self, ticker: str, iv: float, asof: date) -> float | None:
        """Percentile (0-100) of `iv` within this ticker's history BEFORE
        `asof`, or None with fewer than MIN_OBSERVATIONS days recorded."""
        today_key = asof.isoformat()
        hist = [v for d, v in self._data.get(ticker, {}).items() if d < today_key]
        if len(hist) < MIN_OBSERVATIONS:
            return None
        below = sum(1 for v in hist if v <= iv)
        return round(below / len(hist) * 100.0, 1)
