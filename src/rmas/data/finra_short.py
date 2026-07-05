"""FINRA consolidated short interest — free, anonymous, datacenter-friendly.

Re-arms strategy B (squeeze watch), which was disabled while short interest
had no real source. FINRA publishes bi-weekly settlements (~3 weeks max
staleness — fine for a squeeze filter, SI structure moves slowly); records
older than MAX_STALE_DAYS are treated as unknown rather than served stale.

Anonymous POST against the public Query API, one request per candidate per
day (cached). All failures -> None (strategy B then blocks, honest default).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from rmas.config import Secrets, is_offline
from rmas.data import cache
from rmas.logging_setup import get_logger

log = get_logger("data.finra")

_URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
MAX_STALE_DAYS = 45


class FinraShortInterest:
    def __init__(self, secrets: Secrets | None = None, offline: bool | None = None):
        self.secrets = secrets or Secrets()
        self.offline = is_offline(self.secrets) if offline is None else offline

    def latest(self, ticker: str) -> dict | None:
        """{"short_shares", "days_to_cover", "settlement_date"} or None."""
        if self.offline:
            return None
        day = datetime.now(timezone.utc).date().isoformat()
        cached = cache.get("finra_short", f"{ticker.upper()}_{day}")
        if cached is not None:
            return cached or None
        try:  # pragma: no cover - live network
            import requests

            start = (datetime.now(timezone.utc) - timedelta(days=70)).date()
            r = requests.post(_URL, json={
                "compareFilters": [{"fieldName": "symbolCode",
                                    "compareType": "EQUAL",
                                    "fieldValue": ticker.upper()}],
                "dateRangeFilters": [{"fieldName": "settlementDate",
                                      "startDate": start.isoformat(),
                                      "endDate": day}],
                "limit": 10,
            }, headers={"Accept": "application/json"}, timeout=15)
            r.raise_for_status()
            rows = r.json()
            result = _latest_row(rows if isinstance(rows, list) else [])
            cache.put("finra_short", f"{ticker.upper()}_{day}", result or {})
            return result
        except Exception as exc:  # pragma: no cover
            log.warning("finra short interest failed for %s (%s)", ticker, exc)
            return None

    def short_interest_pct(self, ticker: str,
                           float_shares: float | None) -> float | None:
        """Short position as % of shares outstanding. None when unknown."""
        rec = self.latest(ticker)
        if not rec or not float_shares:
            return None
        return round(rec["short_shares"] / float_shares * 100.0, 2)


def _latest_row(rows: list[dict]) -> dict | None:
    """Pick the newest, non-stale settlement (pure, testable)."""
    best: dict | None = None
    for r in rows:
        try:
            sd = date.fromisoformat(str(r.get("settlementDate", "")))
            shares = float(r.get("currentShortPositionQuantity") or 0)
        except (ValueError, TypeError):
            continue
        if shares <= 0:
            continue
        if best is None or sd.isoformat() > best["settlement_date"]:
            best = {"short_shares": shares,
                    "days_to_cover": float(r.get("daysToCoverQuantity") or 0),
                    "settlement_date": sd.isoformat()}
    if best is None:
        return None
    age = (datetime.now(timezone.utc).date()
           - date.fromisoformat(best["settlement_date"])).days
    return best if age <= MAX_STALE_DAYS else None
