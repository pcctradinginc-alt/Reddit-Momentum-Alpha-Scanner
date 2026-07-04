"""CI gate: exit 0 if today (US/Eastern) is a US trading day, else exit 1.

Stdlib only — runs on a bare python3 BEFORE `pip install`, so a holiday run
costs a few seconds of CI instead of a full install + scan + API calls.

    python3 scripts/trading_day.py [YYYY-MM-DD]
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rmas.calendar_us import is_trading_day  # noqa: E402


def _us_eastern_today() -> date:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:  # no tzdata -> fixed UTC-5 (safe: scan runs pre-market)
        return (datetime.now(timezone.utc) - timedelta(hours=5)).date()


def main() -> int:
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else _us_eastern_today()
    open_ = is_trading_day(d)
    print(f"{d} is {'a trading day' if open_ else 'NOT a trading day (weekend/holiday)'}")
    return 0 if open_ else 1


if __name__ == "__main__":
    raise SystemExit(main())
