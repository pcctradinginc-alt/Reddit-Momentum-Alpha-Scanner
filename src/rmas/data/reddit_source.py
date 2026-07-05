"""Reddit ingestion with four modes, chosen automatically:

  1. OAuth via PRAW      — if REDDIT_CLIENT_ID/SECRET are set (most robust).
  2. Public JSON no-auth — if online but no app credentials. Reads the public
     ``/r/<sub>/new.json`` endpoints with a descriptive User-Agent. No client_id
     or secret needed — handy when you can't create a Reddit app. Lighter rate
     limits and no author-age data, but fine for a modest daily scan.
  3. RSS/Atom no-auth    — ``/r/<sub>/new/.rss``. Reddit's IP blocking of the
     JSON endpoints often does NOT cover the RSS feeds, so this is the
     workaround when public JSON returns 403. Same post data (title, body,
     author, timestamp); no score/author-age.
  4. Synthetic           — offline / on any failure, so the pipeline never dies.

Credentials (when used) come only from the environment, never hard-coded.
"""

from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from rmas.config import Secrets, is_offline
from rmas.data.base import SyntheticReddit
from rmas.logging_setup import get_logger
from rmas.types import Mention

log = get_logger("data.reddit")

_DEFAULT_UA = "rmas/0.1 (personal research scanner; public json)"

LIVE_MODES = ("oauth", "public_json", "rss")

_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
_TAG_RE = re.compile(r"<[^>]+>")


def _parse_reddit_atom(payload: bytes, sub: str, cutoff: float) -> list[Mention]:
    """Parse a Reddit ``/new/.rss`` Atom feed into Mentions (pure, testable)."""
    root = ET.fromstring(payload)
    out: list[Mention] = []
    for e in root.findall("a:entry", _ATOM_NS):
        updated = e.findtext("a:updated", default="", namespaces=_ATOM_NS)
        try:
            ts = datetime.fromisoformat(updated)
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts.timestamp() < cutoff:
            continue
        author = (e.findtext("a:author/a:name", default="[deleted]",
                             namespaces=_ATOM_NS) or "[deleted]").removeprefix("/u/")
        title = e.findtext("a:title", default="", namespaces=_ATOM_NS)
        body_html = e.findtext("a:content", default="", namespaces=_ATOM_NS)
        body = html.unescape(_TAG_RE.sub(" ", html.unescape(body_html)))
        link = e.find("a:link", _ATOM_NS)
        out.append(Mention(
            ticker="",
            author=author,
            subreddit=sub,
            created_utc=ts,
            text=f"{title}\n{body}".strip(),
            score=0,                      # not exposed via RSS
            is_submission=True,
            permalink=link.get("href", "") if link is not None else "",
            author_age_days=None,         # not exposed via RSS
        ))
    return out


class RedditAdapter:
    def __init__(self, secrets: Secrets | None = None, offline: bool | None = None,
                 synthetic_tickers: list[str] | None = None):
        self.secrets = secrets or Secrets()
        self.offline = is_offline(self.secrets) if offline is None else offline
        self._synthetic = SyntheticReddit(synthetic_tickers)
        self._client = None
        # Records which path produced the data: "oauth" | "public_json" |
        # "rss" | "synthetic". Consumers use `is_live` to avoid acting on
        # fake data.
        self.mode: str = "synthetic"

    @property
    def is_live(self) -> bool:
        """True only when mentions came from real Reddit (any live mode)."""
        return self.mode in LIVE_MODES

    # ------------------------------------------------------------------ #
    def fetch_mentions(self, subreddits: list[str], lookback_hours: int = 24,
                       limit_per_sub: int = 200) -> list[Mention]:
        if self.offline:
            self.mode = "synthetic"
            log.info("reddit: offline/synthetic mode")
            return self._synthetic.fetch_mentions(subreddits, lookback_hours)

        cutoff = datetime.now(timezone.utc).timestamp() - lookback_hours * 3600

        if self.secrets.reddit_ready:
            out = self._fetch_praw(subreddits, cutoff, limit_per_sub)
            if out is not None:
                self.mode = "oauth"
                return out

        # No app credentials (or PRAW failed) but we're online -> public JSON.
        out = self._fetch_public_json(subreddits, cutoff, limit_per_sub)
        if out is not None:
            self.mode = "public_json"
            return out

        # JSON blocked (403 IP-blocking) -> RSS feeds usually still work.
        out = self._fetch_rss(subreddits, cutoff, limit_per_sub)
        if out is not None:
            self.mode = "rss"
            return out

        self.mode = "synthetic"
        log.warning("reddit: all live paths failed; synthetic fallback")
        return self._synthetic.fetch_mentions(subreddits, lookback_hours)

    # ------------------------------------------------------------------ #
    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            import praw  # type: ignore

            self._client = praw.Reddit(
                client_id=self.secrets.get("REDDIT_CLIENT_ID"),
                client_secret=self.secrets.get("REDDIT_CLIENT_SECRET"),
                user_agent=self.secrets.get("REDDIT_USER_AGENT", _DEFAULT_UA),
                check_for_async=False,
            )
            return self._client
        except Exception as exc:  # pragma: no cover - network/deps
            log.warning("PRAW init failed: %s", exc)
            return None

    def _fetch_praw(self, subreddits, cutoff, limit_per_sub) -> list[Mention] | None:
        client = self._ensure_client()
        if client is None:
            return None
        out: list[Mention] = []
        try:  # pragma: no cover - requires live network
            for sub in subreddits:
                for post in client.subreddit(sub).new(limit=limit_per_sub):
                    if post.created_utc < cutoff:
                        continue
                    author = str(post.author) if post.author else "[deleted]"
                    age_days = None
                    try:
                        if post.author and post.author.created_utc:
                            age_days = (datetime.now(timezone.utc).timestamp()
                                        - post.author.created_utc) / 86400.0
                    except Exception:
                        pass
                    out.append(Mention(
                        ticker="",
                        author=author,
                        subreddit=sub,
                        created_utc=datetime.fromtimestamp(post.created_utc, tz=timezone.utc),
                        text=f"{post.title}\n{getattr(post, 'selftext', '')}",
                        score=int(getattr(post, "score", 0)),
                        is_submission=True,
                        permalink=getattr(post, "permalink", ""),
                        author_age_days=age_days,
                    ))
            log.info("reddit: OAuth/PRAW mode, %d posts", len(out))
            return out
        except Exception as exc:  # pragma: no cover
            log.warning("reddit PRAW fetch failed (%s)", exc)
            return None

    def _fetch_public_json(self, subreddits, cutoff, limit_per_sub) -> list[Mention] | None:
        """No-auth path: read public /r/<sub>/new.json listings."""
        try:  # pragma: no cover - requires live network
            import requests
        except Exception:
            return None

        ua = self.secrets.get("REDDIT_USER_AGENT") or _DEFAULT_UA
        out: list[Mention] = []
        got_any = False
        for sub in subreddits:
            try:  # pragma: no cover - live network
                r = requests.get(
                    f"https://www.reddit.com/r/{sub}/new.json",
                    headers={"User-Agent": ua},
                    params={"limit": min(limit_per_sub, 100)},
                    timeout=15,
                )
                if r.status_code == 429:
                    log.warning("reddit public json rate-limited on r/%s", sub)
                    time.sleep(2)
                    continue
                r.raise_for_status()
                children = r.json().get("data", {}).get("children", [])
                got_any = True
                for ch in children:
                    d = ch.get("data", {})
                    if d.get("created_utc", 0) < cutoff:
                        continue
                    out.append(Mention(
                        ticker="",
                        author=d.get("author", "[deleted]"),
                        subreddit=sub,
                        created_utc=datetime.fromtimestamp(d.get("created_utc", 0), tz=timezone.utc),
                        text=f"{d.get('title', '')}\n{d.get('selftext', '')}",
                        score=int(d.get("score", 0)),
                        is_submission=True,
                        permalink=d.get("permalink", ""),
                        author_age_days=None,  # not available without an extra call
                    ))
                time.sleep(1)  # be polite to the public endpoint
            except Exception as exc:  # pragma: no cover
                log.warning("reddit public json failed on r/%s (%s)", sub, exc)
                continue
        if not got_any:
            return None
        log.info("reddit: public-JSON (no-auth) mode, %d posts", len(out))
        return out

    def _fetch_rss(self, subreddits, cutoff, limit_per_sub) -> list[Mention] | None:
        """No-auth workaround: Reddit's Atom feeds evade the JSON IP-blocks."""
        try:  # pragma: no cover - requires live network
            import requests
        except Exception:
            return None

        ua = self.secrets.get("REDDIT_USER_AGENT") or _DEFAULT_UA
        out: list[Mention] = []
        got_any = False
        for sub in subreddits:
            try:  # pragma: no cover - live network
                r = requests.get(
                    f"https://www.reddit.com/r/{sub}/new/.rss",
                    headers={"User-Agent": ua},
                    params={"limit": min(limit_per_sub, 100)},
                    timeout=15,
                )
                if r.status_code == 429:      # rate-limited: back off, retry once
                    time.sleep(4)
                    r = requests.get(
                        f"https://www.reddit.com/r/{sub}/new/.rss",
                        headers={"User-Agent": ua},
                        params={"limit": min(limit_per_sub, 100)},
                        timeout=15,
                    )
                if r.status_code != 200:
                    log.warning("reddit rss %s on r/%s", r.status_code, sub)
                    time.sleep(1)
                    continue
                out.extend(_parse_reddit_atom(r.content, sub, cutoff))
                got_any = True
                time.sleep(1)  # be polite to the feed endpoint
            except Exception as exc:  # pragma: no cover
                log.warning("reddit rss failed on r/%s (%s)", sub, exc)
                continue
        if not got_any:
            return None
        log.info("reddit: RSS (no-auth) mode, %d posts", len(out))
        return out
