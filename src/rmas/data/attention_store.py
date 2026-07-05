"""Persistent per-ticker daily mention counts — the REAL discovery history.

The discovery gate needs "mentions today vs the last 7/30 days". Live runs
record each day's observed counts here (a tiny JSON under ``data/state/``,
persisted across CI runs via actions/cache), so z-scores are computed against
genuine history instead of a synthetic baseline.

Cold-start honesty: until a ticker has ``MIN_HISTORY_DAYS`` recorded days, its
series is returned flat (padded with today's count) so z-scores are ~0 and the
gate cannot fire on fabricated acceleration. The system therefore needs about
a week of live runs before it starts alerting — by design, not a bug.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from rmas.config import ROOT
from rmas.logging_setup import get_logger

log = get_logger("data.attention_store")

STATE_PATH = ROOT / "data" / "state" / "attention_history.json"
MIN_HISTORY_DAYS = 5
KEEP_DAYS = 45


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _mentions_of(v) -> float:
    """Day value -> mention count. v is legacy float or [mentions, authors]."""
    return float(v[0]) if isinstance(v, list) else float(v)


def _authors_of(v) -> float | None:
    """Day value -> unique-author count, None for legacy entries."""
    return float(v[1]) if isinstance(v, list) and len(v) > 1 else None


class AttentionStore:
    """{ticker: {"YYYY-MM-DD": [mentions, authors]}} with pruning + padded
    series access. Legacy plain-float day values (schema v1) are read as
    mentions-only."""

    def __init__(self, path: Path | None = None):
        self.path = path or STATE_PATH
        self._data: dict[str, dict[str, float]] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                self._data = json.loads(self.path.read_text())
        except Exception as exc:
            log.warning("attention store unreadable (%s); starting fresh", exc)
            self._data = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, sort_keys=True))
        except Exception as exc:
            log.warning("attention store save failed (%s)", exc)

    # ------------------------------------------------------------------ #
    def record(self, asof: date, counts: dict[str, int],
               authors: dict[str, int] | None = None) -> None:
        """Record today's observed mention (and unique-author) counts."""
        key = asof.isoformat()
        authors = authors or {}
        for ticker, n in counts.items():
            self._data.setdefault(ticker, {})[key] = [
                float(n), float(authors.get(ticker, 0))]
        cutoff = (asof - timedelta(days=KEEP_DAYS)).isoformat()
        for ticker in list(self._data):
            days = {d: v for d, v in self._data[ticker].items() if d >= cutoff}
            if days:
                self._data[ticker] = days
            else:
                del self._data[ticker]

    def observed_days(self, ticker: str, before: date) -> int:
        """Number of recorded days strictly before `before`."""
        key = before.isoformat()
        return sum(1 for d in self._data.get(ticker, {}) if d < key)

    def series(self, ticker: str, asof: date, days: int = 30) -> list[float]:
        """Daily mention series of length `days`, ending at `asof` (inclusive).

        Missing days are padded with the median of the observed history. With
        fewer than MIN_HISTORY_DAYS observations the whole series is padded
        flat with today's count -> z ~ 0 -> conservative cold start.
        """
        return self._padded_series(ticker, asof, days, _mentions_of)

    def authors_series(self, ticker: str, asof: date, days: int = 7) -> list[float]:
        """Daily unique-author series — same padding/cold-start rules."""
        return self._padded_series(ticker, asof, days, _authors_of)

    def _padded_series(self, ticker: str, asof: date, days: int,
                       extract) -> list[float]:
        rec = self._data.get(ticker, {})
        today_key = asof.isoformat()
        today = extract(rec.get(today_key, 0.0)) or 0.0

        observed = [extract(v) for d, v in rec.items() if d < today_key]
        observed = [v for v in observed if v is not None]
        if len(observed) < MIN_HISTORY_DAYS:
            return [float(today)] * days

        pad = _median(observed)
        out: list[float] = []
        for i in range(days - 1, -1, -1):
            key = (asof - timedelta(days=i)).isoformat()
            v = extract(rec[key]) if key in rec else None
            out.append(float(v) if v is not None else pad)
        return out
