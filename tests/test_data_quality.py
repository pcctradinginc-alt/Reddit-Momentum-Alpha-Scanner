"""Data-quality fixes: RSS pagination, hype rank, consolidated volume,
no fabricated signals in live mode."""

from datetime import datetime, timedelta, timezone

from rmas.config import Secrets
from rmas.data.apewisdom_source import ApeWisdomAdapter
from rmas.data.fundamentals_finnhub import FinnhubFundamentals
from rmas.data.reddit_source import RedditAdapter
from rmas.data.trends_source import TrendsAdapter


def _atom_page(ids_and_hours: list[tuple[str, float]]) -> bytes:
    now = datetime.now(timezone.utc)
    entries = "".join(
        f"""<entry><id>{eid}</id>
        <author><name>/u/user_{eid}</name></author>
        <title>post {eid}</title>
        <content type="html">body {eid}</content>
        <link href="https://reddit.com/{eid}"/>
        <updated>{(now - timedelta(hours=h)).isoformat()}</updated></entry>"""
        for eid, h in ids_and_hours
    )
    return (f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
            f"{entries}</feed>").encode()


def test_rss_pagination_follows_after_until_cutoff(monkeypatch):
    monkeypatch.setattr("rmas.data.reddit_source.time.sleep", lambda s: None)
    a = RedditAdapter(Secrets(env={}), offline=False)
    pages = {
        None: _atom_page([("t3_a1", 1), ("t3_a2", 2)]),
        "t3_a2": _atom_page([("t3_b1", 3), ("t3_b2", 4)]),
        "t3_b2": _atom_page([("t3_c1", 30)]),   # beyond 24h -> stop
    }
    calls: list[str | None] = []

    def fake_page(sub, after, limit):
        calls.append(after)
        return pages[after]

    a._get_rss_page = fake_page
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).timestamp()
    mentions, subs_ok = a._fetch_rss(["stocks"], cutoff, limit_per_sub=200)
    assert subs_ok == 1
    assert [m.permalink[-5:] for m in mentions] == ["t3_a1", "t3_a2", "t3_b1", "t3_b2"]
    assert calls == [None, "t3_a2", "t3_b2"]     # followed after=, stopped at cutoff


def test_rss_pagination_respects_limit(monkeypatch):
    monkeypatch.setattr("rmas.data.reddit_source.time.sleep", lambda s: None)
    a = RedditAdapter(Secrets(env={}), offline=False)
    a._get_rss_page = lambda sub, after, limit: _atom_page([("t3_x", 1), ("t3_y", 2)])
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).timestamp()
    mentions, _ = a._fetch_rss(["stocks"], cutoff, limit_per_sub=2)
    assert len(mentions) == 2                    # one page, no endless loop


def test_x_attention_is_neutral_in_live_mode():
    live = TrendsAdapter(Secrets(env={}), offline=False)
    assert live.x_attention_z("GME") == 0.0      # never fabricated noise live
    offline = TrendsAdapter(Secrets(env={}), offline=True)
    assert offline.x_attention_z("GME") != 0.0   # synthetic demo value


def test_finnhub_avg_dollar_volume_converts_millions():
    f = FinnhubFundamentals(Secrets(env={}), offline=False)
    f.metrics = lambda t: {"10DayAverageTradingVolume": 12.5}   # millions of shares
    assert f.avg_dollar_volume_usd("NVDA", close=100.0) == 12.5e6 * 100.0
    f.metrics = lambda t: {}
    assert f.avg_dollar_volume_usd("NVDA", close=100.0) is None
    f.metrics = lambda t: {"10DayAverageTradingVolume": "n/a"}
    assert f.avg_dollar_volume_usd("NVDA", close=100.0) is None


def test_apewisdom_offline_is_conservative():
    a = ApeWisdomAdapter(Secrets(env={}), offline=True)
    assert a.top() == {}
    assert a.rank("GME") is None                 # unknown -> not top-hype


def test_apewisdom_rank_lookup():
    a = ApeWisdomAdapter(Secrets(env={}), offline=False)
    a._fetched = True
    a._top = {"MU": {"rank": 1, "mentions": 112, "mentions_24h_ago": 162, "upvotes": 1058}}
    assert a.rank("mu") == 1                     # case-insensitive
    assert a.rank("TSLA") is None
