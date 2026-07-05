"""SEC EDGAR filings — free, keyless, works from GitHub runners.

Two jobs:
  * REAL "days since earnings" for strategy C (post-earnings drift), which
    was silently dead before: an 8-K with item 2.02 (results of operations)
    or a 10-Q/10-K filing marks an earnings event.
  * Recent-filing pseudo-headlines for the catalyst detector (a real 8-K
    beats reddit chatter as catalyst evidence).

SEC etiquette: a descriptive User-Agent with contact is required; volume
here is ~25 requests/day, far under their 10 req/s guidance. Responses are
cached per day. All failures degrade to None/[] — offline behavior is
unchanged (strategy C then blocks exactly as before).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from rmas.config import Secrets, is_offline
from rmas.data import cache
from rmas.logging_setup import get_logger

log = get_logger("data.sec")

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:0>10}.json"
_UA = {"User-Agent": "rmas research scanner (pcctradinginc@gmail.com)"}
EARNINGS_FORMS = ("10-Q", "10-K")
EARNINGS_8K_ITEM = "2.02"
LOOKBACK_FILINGS = 40           # most-recent filings to inspect


class SECEdgarAdapter:
    def __init__(self, secrets: Secrets | None = None, offline: bool | None = None):
        self.secrets = secrets or Secrets()
        self.offline = is_offline(self.secrets) if offline is None else offline

    # ------------------------------------------------------------------ #
    def _cik(self, ticker: str) -> int | None:
        if self.offline:
            return None
        day = datetime.now(timezone.utc).date().isoformat()
        mapping = cache.get("sec_tickers", day)
        if mapping is None:
            try:  # pragma: no cover - live network
                import requests

                r = requests.get(_TICKERS_URL, headers=_UA, timeout=15)
                r.raise_for_status()
                mapping = {v["ticker"].upper(): v["cik_str"]
                           for v in r.json().values()}
                cache.put("sec_tickers", day, mapping)
            except Exception as exc:  # pragma: no cover
                log.warning("sec ticker map failed (%s)", exc)
                return None
        return mapping.get(ticker.upper())

    def recent_filings(self, ticker: str, days: int = 7,
                       asof: date | None = None) -> list[dict]:
        """[{form, date, items}] filed within `days` before `asof`."""
        if self.offline:
            return []
        cik = self._cik(ticker)
        if cik is None:
            return []
        asof = asof or datetime.now(timezone.utc).date()
        day_key = f"{ticker.upper()}_{asof.isoformat()}"
        recent = cache.get("sec_filings", day_key)
        if recent is None:
            try:  # pragma: no cover - live network
                import requests

                r = requests.get(_SUBMISSIONS_URL.format(cik=cik),
                                 headers=_UA, timeout=15)
                r.raise_for_status()
                rec = (r.json().get("filings") or {}).get("recent") or {}
                recent = [
                    {"form": f, "date": d, "items": i}
                    for f, d, i in zip(rec.get("form", [])[:LOOKBACK_FILINGS],
                                       rec.get("filingDate", [])[:LOOKBACK_FILINGS],
                                       (rec.get("items") or [""] * LOOKBACK_FILINGS)[:LOOKBACK_FILINGS])
                ]
                cache.put("sec_filings", day_key, recent)
            except Exception as exc:  # pragma: no cover
                log.warning("sec submissions failed for %s (%s)", ticker, exc)
                return []
        return _filter_recent(recent, asof, days)

    def days_since_earnings(self, ticker: str,
                            asof: date | None = None) -> int | None:
        """Days since the last earnings event (8-K item 2.02 or 10-Q/10-K),
        looking back 90 days. None when unknown."""
        asof = asof or datetime.now(timezone.utc).date()
        filings = self.recent_filings(ticker, days=90, asof=asof)
        best: int | None = None
        for f in filings:
            if not _is_earnings_filing(f):
                continue
            try:
                dse = (asof - date.fromisoformat(f["date"])).days
            except ValueError:
                continue
            if dse >= 0 and (best is None or dse < best):
                best = dse
        return best


def _is_earnings_filing(f: dict) -> bool:
    if f.get("form") in EARNINGS_FORMS:
        return True
    return f.get("form") == "8-K" and EARNINGS_8K_ITEM in str(f.get("items", ""))


def _filter_recent(filings: list[dict], asof: date, days: int) -> list[dict]:
    out = []
    for f in filings:
        try:
            age = (asof - date.fromisoformat(f.get("date", ""))).days
        except ValueError:
            continue
        if 0 <= age <= days:
            out.append(f)
    return out


def filings_as_headlines(filings: list[dict]) -> list[str]:
    """Render filings as pseudo-headlines for the catalyst detector
    (its keyword table already scores '8-k', '10-q', 'filing')."""
    return [f"{f['form']} SEC filing {f['date']}"
            + (f" (items {f['items']})" if f.get("items") else "")
            for f in filings]
