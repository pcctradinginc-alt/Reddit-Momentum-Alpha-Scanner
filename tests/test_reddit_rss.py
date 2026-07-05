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
    mentions = _parse_reddit_atom(_feed_bytes(), "stocks", cutoff)
    assert len(mentions) == 1
    m = mentions[0]
    assert m.author == "trader_one"                  # "/u/" prefix stripped
    assert m.subreddit == "stocks"
    assert "NVDA breakout thread" in m.text
    assert "$NVDA looks strong" in m.text            # html unescaped + tags stripped
    assert "<p>" not in m.text
    assert m.permalink.endswith("/abc/post/")
    assert m.is_submission


def test_parse_atom_tolerates_garbage_dates():
    feed = _feed_bytes().replace(b"stale post", b"x").replace(
        (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat().encode()[:10],
        b"not-a-date",
    )
    # must not raise; bad entry is skipped
    mentions = _parse_reddit_atom(feed, "stocks", 0)
    assert all("not-a-date" not in m.text for m in mentions)


def test_rss_counts_as_live_mode():
    assert "rss" in LIVE_MODES
