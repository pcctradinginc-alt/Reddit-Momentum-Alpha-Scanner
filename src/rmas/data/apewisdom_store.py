"""Persistent per-ticker ApeWisdom daily mention counts.

Backs ``ApeWisdomAdapter.attention_accel_z`` (see apewisdom_source.py).
ApeWisdom aggregates Reddit itself (INCLUDING comments, across many subs), so
this store gives a same-channel, richer view of "how much is this ticker
being discussed" than our own RSS radar sees day-to-day. Mirrors
attention_store.py's persistence + honest-cold-start design: until a ticker
has ``MIN_HISTORY_DAYS`` recorded days, its series is returned flat (padded
with today's value) so z-scores start at ~0 instead of fabricating
acceleration from a single data point.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from rmas.config import ROOT
from rmas.logging_setup import get_logger

log = get_logger("data.apewisdom_store")

STATE_PATH = ROOT / "data" / "state" / "apewisdom_history.json"
MIN_HISTORY_DAYS = 5
KEEP_DAYS = 45


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


class ApeWisdomStore:
    """{ticker: {"YYYY-MM-DD": mentions}} with pruning + padded series access.

    Never raises: IO/parse failures degrade to an empty (fresh) store.
    """

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else STATE_PATH
        self._data: dict[str, dict[str, float]] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                self._data = json.loads(self.path.read_text())
        except Exception as exc:
            log.warning("apewisdom store unreadable (%s); starting fresh", exc)
            self._data = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, sort_keys=True))
        except Exception as exc:
            log.warning("apewisdom store save failed (%s)", exc)

    # ------------------------------------------------------------------ #
    def record(self, asof: date, counts: dict[str, float]) -> None:
        """Record today's observed mention counts, then prune old days."""
        key = asof.isoformat()
        for ticker, n in counts.items():
            self._data.setdefault(ticker, {})[key] = float(n)
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

        Missing days are padded with the median of observed history. With
        fewer than MIN_HISTORY_DAYS observations the whole series is padded
        flat with today's count -> z ~ 0 -> conservative cold start.
        """
        rec = self._data.get(ticker, {})
        today_key = asof.isoformat()
        today = float(rec.get(today_key, 0.0))

        observed = [float(v) for d, v in rec.items() if d < today_key]
        if len(observed) < MIN_HISTORY_DAYS:
            return [today] * days

        pad = _median(observed)
        out: list[float] = []
        for i in range(days - 1, -1, -1):
            key = (asof - timedelta(days=i)).isoformat()
            out.append(float(rec[key]) if key in rec else pad)
        return out
