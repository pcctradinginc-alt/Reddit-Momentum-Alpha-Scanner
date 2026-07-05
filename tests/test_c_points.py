"""C-points: honest liquidity, sector mapping, real hourly series,
author history, strategy enabled-flags."""

from datetime import date, datetime, timedelta, timezone

from rmas.config import Secrets, load_config
from rmas.data.attention_store import AttentionStore
from rmas.data.market_alpaca import AlpacaAdapter
from rmas.features.sector_map import SECTOR_ETFS, sector_etf
from rmas.pipeline.scan import _hourly_counts
from rmas.types import Bar, Mention


# ---------------------------------------------------------------- sector map
def test_sector_map_common_industries():
    assert sector_etf("Semiconductors") == "XLK"
    assert sector_etf("Software") == "XLK"
    assert sector_etf("Banking") == "XLF"
    assert sector_etf("Biotechnology") == "XLV"
    assert sector_etf("Oil, Gas & Consumable Fuels") == "XLE"
    assert sector_etf("Metals & Mining") == "XLB"
    assert sector_etf("Airlines") == "XLI"
    assert sector_etf("Electric Utilities") == "XLU"
    assert sector_etf("Equity Real Estate Investment Trusts (REITs)") == "XLRE"
    assert sector_etf("Media") == "XLC"
    assert sector_etf("Beverages") == "XLP"


def test_sector_map_specific_beats_generic():
    # "...Retail" must map to consumer discretionary, not internet/XLC
    assert sector_etf("Internet & Direct Marketing Retail") == "XLY"
    assert sector_etf("Internet") == "XLC"


def test_sector_map_unknown_is_none():
    assert sector_etf("Frobnication Services") is None
    assert sector_etf(None) is None
    assert sector_etf("") is None
    assert "SPY" not in SECTOR_ETFS


# ------------------------------------------------------- honest live liquidity
def _bars(close=50.0, volume=2_000_000):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [Bar(t=t0 + timedelta(days=i), open=close, high=close, low=close,
                close=close, volume=volume) for i in range(5)]


def test_live_liquidity_never_fabricates(monkeypatch):
    a = AlpacaAdapter(Secrets(env={"ALPACA_API_KEY": "k", "ALPACA_API_SECRET": "s"}),
                      offline=False)
    monkeypatch.setattr(a, "spread_bps", lambda t: 12.0)
    liq = a.liquidity("XYZ", bars=_bars(), market_cap_usd=2e9,
                      dollar_volume_usd=9e7, float_shares=1e8)
    assert liq.market_cap_usd == 2e9
    assert liq.dollar_volume_usd == 9e7
    assert liq.float_shares == 1e8
    assert liq.short_interest_pct == 0.0          # unknown -> neutral, not random
    assert liq.borrow_available is True

    # missing fundamentals -> fail-closed cap (0), bar-based $-volume
    liq2 = a.liquidity("XYZ", bars=_bars(close=10.0, volume=1000))
    assert liq2.market_cap_usd == 0.0
    assert liq2.dollar_volume_usd == 10.0 * 1000


def test_offline_liquidity_stays_synthetic():
    a = AlpacaAdapter(Secrets(env={}), offline=True)
    liq = a.liquidity("XYZ")
    assert liq.market_cap_usd > 0                 # deterministic synthetic demo


# ------------------------------------------------------------- hourly series
def test_hourly_counts_from_timestamps():
    asof = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)

    def m(hours_ago: float, sub=True):
        return Mention(ticker="T", author="a", subreddit="s",
                       created_utc=asof - timedelta(hours=hours_ago),
                       text="", is_submission=sub)

    ms = [m(0.5), m(0.7), m(1.5), m(6.5), m(9.0), m(0.2, sub=False)]
    threads = _hourly_counts(ms, asof, hours=7, submissions=True)
    comments = _hourly_counts(ms, asof, hours=7, submissions=False)
    assert threads == [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 2.0]   # 9h-old excluded
    assert comments[-1] == 1.0 and sum(comments) == 1.0


# ------------------------------------------------------ author history (v2)
def test_author_series_and_v1_backward_compat(tmp_path):
    path = tmp_path / "attn.json"
    s = AttentionStore(path=path)
    start = date(2026, 6, 1)
    # legacy v1 float entries (no author info)
    for i in range(3):
        s._data.setdefault("PLTR", {})[(start + timedelta(days=i)).isoformat()] = 4.0
    # v2 entries
    for i in range(3, 9):
        s.record(start + timedelta(days=i), {"PLTR": 5}, {"PLTR": 3})
    today = start + timedelta(days=9)
    s.record(today, {"PLTR": 40}, {"PLTR": 25})

    assert s.series("PLTR", today, days=10)[-1] == 40.0     # mentions incl. v1 days
    authors = s.authors_series("PLTR", today, days=7)
    assert authors[-1] == 25.0
    assert authors[-2] == 3.0

    # roundtrip through disk keeps both
    s.save()
    s2 = AttentionStore(path=path)
    assert s2.authors_series("PLTR", today, days=7)[-1] == 25.0


def test_author_cold_start_flat(tmp_path):
    s = AttentionStore(path=tmp_path / "a.json")
    today = date(2026, 7, 1)
    s.record(today, {"GME": 30}, {"GME": 20})
    assert s.authors_series("GME", today, days=7) == [20.0] * 7


# ----------------------------------------------------- strategy enabled flag
def test_strategy_b_disabled_in_config():
    cfg = load_config()
    strategies = cfg.to_dict()["strategies"]
    assert strategies["B_squeeze_watch"]["enabled"] is False
    assert strategies["A_early_momentum_long"]["enabled"] is True
