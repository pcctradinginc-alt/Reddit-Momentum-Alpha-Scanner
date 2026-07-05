"""Reddit RSS/Atom fallback: parser correctness + live-mode accounting."""

from datetime import datetime, timedelta, timezone

from rmas.data.reddit_source import LIVE_MODES, _parse_reddit_atom

_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>newest submissions : stocks</title>
  <entry>
    <author><name>/u/trader_one</name><uri>https://www.reddit.com/user/trader_one</uri></author>
    <content type="html">&lt;div class="md"&gt;&lt;p&gt;$NVDA looks strong, watching the breakout&lt;/p&gt;&lt;/div&gt;</content>
    <link href="https://www.reddit.com/r/stocks/comments/abc/post/"/>
    <updated>{fresh}</updated>
    <title>NVDA breakout thread</title>
  </entry>
  <entry>
    <author><name>/u/old_poster</name></author>
    <content type="html">stale post</content>
    <link href="https://www.reddit.com/r/stocks/comments/xyz/old/"/>
    <updated>{stale}</updated>
    <title>old news</title>
  </entry>
</feed>"""


def _feed_bytes() -> bytes:
    now = datetime.now(timezone.utc)
    return _FEED.replace(
        b"{fresh}", (now - timedelta(hours=1)).isoformat().encode()
    ).replace(
        b"{stale}", (now - timedelta(hours=50)).isoformat().encode()
    )


def test_parse_atom_extracts_fresh_entries_only():
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).timestamp()
    mentions, last_id, reached_cutoff = _parse_reddit_atom(_feed_bytes(), "stocks", cutoff)
    assert len(mentions) == 1
    m = mentions[0]
    assert m.author == "trader_one"                  # "/u/" prefix stripped
    assert m.subreddit == "stocks"
    assert "NVDA breakout thread" in m.text
    assert "$NVDA looks strong" in m.text            # html unescaped + tags stripped
    assert "<p>" not in m.text
    assert m.permalink.endswith("/abc/post/")
    assert m.is_submission
    assert reached_cutoff                            # the stale entry crossed the window


def test_parse_atom_tolerates_garbage_dates():
    feed = _feed_bytes().replace(b"stale post", b"x").replace(
        (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat().encode()[:10],
        b"not-a-date",
    )
    # must not raise; bad entry is skipped
    mentions, _, _ = _parse_reddit_atom(feed, "stocks", 0)
    assert all("not-a-date" not in m.text for m in mentions)


def test_rss_counts_as_live_mode():
    assert "rss" in LIVE_MODES


def _online_adapter(min_coverage=0.5):
    from rmas.config import Secrets
    from rmas.data.reddit_source import RedditAdapter

    return RedditAdapter(Secrets(env={}), offline=False, min_coverage=min_coverage)


_SUBS = ["a", "b", "c", "d", "e", "f", "g"]


def _mention():
    from rmas.types import Mention

    return Mention(ticker="", author="x", subreddit="a",
                   created_utc=datetime.now(timezone.utc), text="hi $NVDA")


def test_low_coverage_falls_back_to_synthetic():
    """1 of 7 subs answering must NOT count as a live radar."""
    a = _online_adapter()
    a._fetch_public_json = lambda *args: None
    a._fetch_rss = lambda *args: ([_mention()], 1)
    a.fetch_mentions(_SUBS, 24)
    assert a.mode == "synthetic"
    assert not a.is_live


def test_good_coverage_is_live():
    a = _online_adapter()
    a._fetch_public_json = lambda *args: None
    a._fetch_rss = lambda *args: ([_mention()] * 30, 6)
    out = a.fetch_mentions(_SUBS, 24)
    assert a.mode == "rss" and a.is_live
    assert abs(a.coverage - 6 / 7) < 1e-9
    assert len(out) == 30


def test_partial_json_falls_through_to_full_rss():
    a = _online_adapter()
    a._fetch_public_json = lambda *args: ([_mention()], 2)   # 2/7 too thin
    a._fetch_rss = lambda *args: ([_mention()] * 10, 7)
    a.fetch_mentions(_SUBS, 24)
    assert a.mode == "rss"
    assert a.coverage == 1.0
