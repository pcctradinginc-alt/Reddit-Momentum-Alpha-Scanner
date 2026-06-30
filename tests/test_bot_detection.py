from datetime import datetime, timezone

from rmas.nlp.bot_detection import BotConfig, bot_ratio, emoji_ratio, jaccard, score_mentions
from rmas.types import Mention


def _m(author, text, age=200.0, sub="wallstreetbets"):
    return Mention(ticker="ABC", author=author, subreddit=sub,
                   created_utc=datetime.now(timezone.utc), text=text, author_age_days=age)


def test_jaccard_identical():
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_emoji_ratio_detects_overload():
    assert emoji_ratio("🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀") > 0.5
    assert emoji_ratio("a normal sentence about stocks") == 0.0


def test_fresh_account_more_suspicious_than_old():
    ms = [_m("new", "buy ABC now", age=1.0), _m("old", "buy ABC now too", age=900.0)]
    score_mentions(ms, BotConfig())
    new_prob = next(m.bot_probability for m in ms if m.author == "new")
    old_prob = next(m.bot_probability for m in ms if m.author == "old")
    assert new_prob > old_prob


def test_coordinated_duplicates_flagged():
    text = "ABC TO THE MOON BUY NOW HUGE SQUEEZE INCOMING"
    ms = [_m(f"u{i}", text) for i in range(6)]
    score_mentions(ms, BotConfig())
    assert bot_ratio(ms, threshold=0.4) > 0.0


def test_organic_low_bot_ratio():
    ms = [
        _m("alice", "I think ABC has a decent setup after earnings"),
        _m("bob", "Watching ABC, volume picking up but not crazy"),
        _m("carol", "ABC reclaimed its 20d high today, interesting"),
    ]
    score_mentions(ms, BotConfig())
    assert bot_ratio(ms, threshold=0.6) == 0.0
