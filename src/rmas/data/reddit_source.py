"""Reddit ingestion via PRAW, with offline synthetic fallback.

Keys are read from the environment (never hard-coded). When offline or when
PRAW/credentials are unavailable, returns deterministic synthetic mentions so
the pipeline still runs.
"""

from __future__ import annotations

from datetime import datetime, timezone

from rmas.config import Secrets, is_offline
from rmas.data.base import SyntheticReddit
from rmas.logging_setup import get_logger
from rmas.types import Mention

log = get_logger("data.reddit")


class RedditAdapter:
    def __init__(self, secrets: Secrets | None = None, offline: bool | None = None,
                 synthetic_tickers: list[str] | None = None):
        self.secrets = secrets or Secrets()
        self.offline = is_offline(self.secrets) if offline is None else offline
        self._synthetic = SyntheticReddit(synthetic_tickers)
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if self.offline or not self.secrets.reddit_ready:
            return None
        try:
            import praw  # type: ignore

            self._client = praw.Reddit(
                client_id=self.secrets.get("REDDIT_CLIENT_ID"),
                client_secret=self.secrets.get("REDDIT_CLIENT_SECRET"),
                user_agent=self.secrets.get("REDDIT_USER_AGENT", "rmas/0.1"),
                check_for_async=False,
            )
            return self._client
        except Exception as exc:  # pragma: no cover - network/deps
            log.warning("PRAW init failed, falling back to synthetic: %s", exc)
            return None

    def fetch_mentions(self, subreddits: list[str], lookback_hours: int = 24,
                       limit_per_sub: int = 200) -> list[Mention]:
        client = self._ensure_client()
        if client is None:
            log.info("reddit: offline/synthetic mode")
            return self._synthetic.fetch_mentions(subreddits, lookback_hours)

        # NOTE: extraction of tickers from text happens in the pipeline; here we
        # just collect raw posts+comments as Mention rows (ticker filled later).
        out: list[Mention] = []
        cutoff = datetime.now(timezone.utc).timestamp() - lookback_hours * 3600
        try:  # pragma: no cover - requires live network
            for sub in subreddits:
                sr = client.subreddit(sub)
                for post in sr.new(limit=limit_per_sub):
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
                        ticker="",  # filled by ticker_extraction over .text
                        author=author,
                        subreddit=sub,
                        created_utc=datetime.fromtimestamp(post.created_utc, tz=timezone.utc),
                        text=f"{post.title}\n{getattr(post, 'selftext', '')}",
                        score=int(getattr(post, "score", 0)),
                        is_submission=True,
                        permalink=getattr(post, "permalink", ""),
                        author_age_days=age_days,
                    ))
        except Exception as exc:  # pragma: no cover
            log.warning("reddit fetch failed (%s); synthetic fallback", exc)
            return self._synthetic.fetch_mentions(subreddits, lookback_hours)
        return out
