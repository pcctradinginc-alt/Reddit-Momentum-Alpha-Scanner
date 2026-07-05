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


def _parse_reddit_atom(payload: bytes, sub: str,
                       cutoff: float) -> tuple[list[Mention], str | None, bool]:
    """Parse a Reddit ``/new/.rss`` Atom feed page (pure, testable).

    Returns ``(mentions, last_entry_id, reached_cutoff)`` — the last ``t3_*``
    id feeds the ``after=`` pagination param, ``reached_cutoff`` says an entry
    older than the lookback window was seen (no point fetching more pages).
    """
    root = ET.fromstring(payload)
    out: list[Mention] = []
    last_id: str | None = None
    reached_cutoff = False
    for e in root.findall("a:entry", _ATOM_NS):
        eid = e.findtext("a:id", default="", namespaces=_ATOM_NS)
        if eid.startswith("t3_"):
            last_id = eid
        updated = e.findtext("a:updated", default="", namespaces=_ATOM_NS)
        try:
            ts = datetime.fromisoformat(updated)
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts.timestamp() < cutoff:
            reached_cutoff = True
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
    return out, last_id, reached_cutoff


class RedditAdapter:
    def __init__(self, secrets: Secrets | None = None, offline: bool | None = None,
                 synthetic_tickers: list[str] | None = None, min_coverage: float = 0.5):
        self.secrets = secrets or Secrets()
        self.offline = is_offline(self.secrets) if offline is None else offline
        self._synthetic = SyntheticReddit(synthetic_tickers)
        self._client = None
        # Records which path produced the data: "oauth" | "public_json" |
        # "rss" | "synthetic". Consumers use `is_live` to avoid acting on
        # fake data.
        self.mode: str = "synthetic"
        # A no-auth path only counts as live when at least this fraction of
        # subreddits answered. Day-to-day coverage swings would otherwise
        # fake "attention acceleration" in the persisted mention history.
        self.min_coverage = min_coverage
        self.coverage: float = 0.0

    @property
    def is_live(self) -> bool:
        """True only when mentions came from real Reddit (any live mode)."""
        return self.mode in LIVE_MODES

    # ------------------------------------------------------------------ #
    def fetch_mentions(self, subreddits: list[str], lookback_hours: int = 24,
                       limit_per_sub: int = 200) -> list[Mention]:
        if self.offline:
            self.mode, self.coverage = "synthetic", 1.0
            log.info("reddit: offline/synthetic mode")
            return self._synthetic.fetch_mentions(subreddits, lookback_hours)

        cutoff = datetime.now(timezone.utc).timestamp() - lookback_hours * 3600
        total = max(1, len(subreddits))

        if self.secrets.reddit_ready:
            out = self._fetch_praw(subreddits, cutoff, limit_per_sub)
            if out is not None:
                self.mode, self.coverage = "oauth", 1.0
                return out

        # No app credentials (or PRAW failed) but we're online -> public JSON.
        for name, fetch in (("public_json", self._fetch_public_json),
                            ("rss", self._fetch_rss)):
            res = fetch(subreddits, cutoff, limit_per_sub)
            if res is None:
                continue
            out, subs_ok = res
            cov = subs_ok / total
            if cov >= self.min_coverage:
                self.mode, self.coverage = name, cov
                return out
            log.warning("reddit %s coverage %d/%d below min %.0f%% — data would "
                        "distort the attention history; trying next path",
                        name, subs_ok, total, self.min_coverage * 100)

        self.mode, self.coverage = "synthetic", 1.0
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

    def _fetch_public_json(self, subreddits, cutoff,
                           limit_per_sub) -> tuple[list[Mention], int] | None:
        """No-auth path: public /r/<sub>/new.json. Returns (mentions, subs_ok)."""
        try:  # pragma: no cover - requires live network
            import requests
        except Exception:
            return None

        ua = self.secrets.get("REDDIT_USER_AGENT") or _DEFAULT_UA
        out: list[Mention] = []
        subs_ok = 0
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
                subs_ok += 1
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
        if not subs_ok:
            return None
        log.info("reddit: public-JSON (no-auth) mode, %d posts from %d subs",
                 len(out), subs_ok)
        return out, subs_ok

    def _get_rss_page(self, sub: str, after: str | None,
                      limit: int) -> bytes | None:
        """One RSS page with 429 backoff. Separated for testability."""
        try:  # pragma: no cover - requires live network
            import requests
        except Exception:
            return None
        ua = self.secrets.get("REDDIT_USER_AGENT") or _DEFAULT_UA
        params: dict[str, str | int] = {"limit": min(limit, 100)}
        if after:
            params["after"] = after
        r = None
        for attempt in range(3):  # pragma: no cover - live network
            r = requests.get(
                f"https://www.reddit.com/r/{sub}/new/.rss",
                headers={"User-Agent": ua}, params=params, timeout=15,
            )
            if r.status_code != 429:
                break
            time.sleep(20 * (attempt + 1))   # 429: back off and retry
        if r is None or r.status_code != 200:  # pragma: no cover
            log.warning("reddit rss %s on r/%s",
                        r.status_code if r is not None else "?", sub)
            return None
        return r.content  # pragma: no cover

    def _fetch_rss(self, subreddits, cutoff,
                   limit_per_sub) -> tuple[list[Mention], int] | None:
        """No-auth workaround: Reddit's Atom feeds evade the JSON IP-blocks.

        Returns (mentions, subs_ok). Paginates via ``after=t3_*`` up to
        limit_per_sub posts so busy days aren't capped at one page — capping
        would compress exactly the attention spikes we want to measure.
        Reddit rate-limits the feeds hard from shared/datacenter IPs, so
        pacing is deliberately slow (once per day, the extra minutes of
        runtime are irrelevant; coverage is not).
        """
        out: list[Mention] = []
        subs_ok = 0
        first_request = True
        deadline = time.monotonic() + 480    # hard budget: never blow the CI timeout
        for sub in subreddits:
            if time.monotonic() > deadline:
                log.warning("reddit rss time budget exhausted before r/%s "
                            "(coverage floor decides live/degraded)", sub)
                break
            got_page = False
            after: str | None = None
            fetched = 0
            while fetched < limit_per_sub and time.monotonic() <= deadline:
                if not first_request:
                    time.sleep(8)             # generous spacing between requests
                first_request = False
                payload = self._get_rss_page(sub, after, limit_per_sub - fetched)
                if payload is None:
                    break
                try:
                    mentions, last_id, reached_cutoff = _parse_reddit_atom(
                        payload, sub, cutoff)
                except ET.ParseError as exc:
                    log.warning("reddit rss parse failed on r/%s (%s)", sub, exc)
                    break
                got_page = True
                out.extend(mentions)
                fetched += max(len(mentions), 1)
                if reached_cutoff or not mentions or last_id is None:
                    break                     # window exhausted / feed ended
                after = last_id
            if got_page:
                subs_ok += 1
        if not subs_ok:
            return None
        log.info("reddit: RSS (no-auth) mode, %d posts from %d subs",
                 len(out), subs_ok)
        return out, subs_ok
