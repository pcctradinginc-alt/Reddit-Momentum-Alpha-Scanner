"""New free alpha sources: SEC EDGAR earnings/filings, FINRA short interest,
ApeWisdom growth confirmation."""

from datetime import date, datetime, timedelta, timezone

from rmas.config import Secrets
from rmas.data.apewisdom_source import ApeWisdomAdapter
from rmas.data.finra_short import _latest_row
from rmas.data.sec_edgar import (
    SECEdgarAdapter,
    _filter_recent,
    _is_earnings_filing,
    filings_as_headlines,
)


# ------------------------------------------------------------------ SEC EDGAR
def test_earnings_filing_detection():
    assert _is_earnings_filing({"form": "10-Q", "items": ""})
    assert _is_earnings_filing({"form": "10-K", "items": ""})
    assert _is_earnings_filing({"form": "8-K", "items": "2.02,9.01"})
    assert not _is_earnings_filing({"form": "8-K", "items": "5.02"})
    assert not _is_earnings_filing({"form": "4", "items": ""})


def test_filter_recent_window():
    asof = date(2026, 7, 6)
    filings = [
        {"form": "8-K", "date": "2026-07-04", "items": ""},
        {"form": "10-Q", "date": "2026-06-01", "items": ""},
        {"form": "8-K", "date": "junk", "items": ""},
    ]
    out = _filter_recent(filings, asof, days=7)
    assert [f["date"] for f in out] == ["2026-07-04"]


def test_days_since_earnings_picks_nearest(tmp_path, monkeypatch):
    a = SECEdgarAdapter(Secrets(env={}), offline=False)
    asof = date(2026, 7, 6)
    monkeypatch.setattr(a, "recent_filings", lambda t, days, asof=None: [
        {"form": "8-K", "date": "2026-07-03", "items": "2.02"},   # earnings, 3d
        {"form": "10-Q", "date": "2026-05-30", "items": ""},      # older
        {"form": "8-K", "date": "2026-07-05", "items": "5.02"},   # not earnings
    ])
    assert a.days_since_earnings("NVDA", asof) == 3


def test_filings_render_as_catalyst_headlines():
    from rmas.features.catalyst import detect_catalyst

    hl = filings_as_headlines([{"form": "8-K", "date": "2026-07-02", "items": "2.02"}])
    assert hl == ["8-K SEC filing 2026-07-02 (items 2.02)"]
    cat = detect_catalyst([], hl)
    assert cat.has_catalyst and "sec_filing" in cat.categories


def test_sec_offline_is_neutral():
    a = SECEdgarAdapter(Secrets(env={}), offline=True)
    assert a.recent_filings("NVDA") == []
    assert a.days_since_earnings("NVDA") is None


# ---------------------------------------------------------------------- FINRA
def _row(sd: str, shares: float, dtc: float = 5.0) -> dict:
    return {"settlementDate": sd, "currentShortPositionQuantity": shares,
            "daysToCoverQuantity": dtc}


def test_finra_latest_row_picks_newest():
    today = datetime.now(timezone.utc).date()
    rows = [_row((today - timedelta(days=30)).isoformat(), 100),
            _row((today - timedelta(days=16)).isoformat(), 200, 8.3),
            _row("garbage", 300)]
    best = _latest_row(rows)
    assert best["short_shares"] == 200 and best["days_to_cover"] == 8.3


def test_finra_stale_data_rejected():
    old = (datetime.now(timezone.utc).date() - timedelta(days=60)).isoformat()
    assert _latest_row([_row(old, 100)]) is None
    assert _latest_row([]) is None
    assert _latest_row([_row("2026-06-15", 0)]) is None    # zero shares = junk


def test_finra_pct_of_float(monkeypatch):
    from rmas.data.finra_short import FinraShortInterest

    f = FinraShortInterest(Secrets(env={}), offline=False)
    monkeypatch.setattr(f, "latest", lambda t: {"short_shares": 30e6,
                                                "days_to_cover": 8.0,
                                                "settlement_date": "2026-06-15"})
    assert f.short_interest_pct("GME", 150e6) == 20.0
    assert f.short_interest_pct("GME", None) is None
    monkeypatch.setattr(f, "latest", lambda t: None)
    assert f.short_interest_pct("GME", 150e6) is None


# ----------------------------------------------------------- ApeWisdom growth
def test_hype_growth_bounded_additive_only():
    a = ApeWisdomAdapter(Secrets(env={}), offline=False)
    a._fetched = True
    a._top = {
        "UP3X": {"rank": 40, "mentions": 90, "mentions_24h_ago": 30, "upvotes": 1},
        "FLAT": {"rank": 50, "mentions": 30, "mentions_24h_ago": 30, "upvotes": 1},
        "DOWN": {"rank": 60, "mentions": 10, "mentions_24h_ago": 40, "upvotes": 1},
        "NEW": {"rank": 70, "mentions": 25, "mentions_24h_ago": 0, "upvotes": 1},
    }
    assert a.growth("UP3X") == 1.0        # 3x growth -> full score
    assert a.growth("FLAT") == 0.0
    assert a.growth("DOWN") == 0.0        # decline never subtracts
    assert a.growth("NEW") == 0.0         # no baseline -> conservative
    assert a.growth("UNKNOWN") == 0.0
