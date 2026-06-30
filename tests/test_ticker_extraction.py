from rmas.nlp.ticker_extraction import ExtractionConfig, count_mentions, extract_tickers

UNIVERSE = {
    "ticker_extraction": {
        "ambiguous_symbols": ["AI", "ON", "NOW", "CAT", "DD", "ARE", "FOR", "ALL", "IT"],
        "hard_blocklist": ["USD", "CPI", "WSB", "YOLO", "DD", "IV", "FOMO"],
        "context_cues": ["calls", "puts", "earnings", "squeeze", "shares", "breakout"],
        "min_symbol_len": 1,
        "max_symbol_len": 5,
    }
}


def cfg(company_map=None):
    return ExtractionConfig.from_universe(UNIVERSE, company_map)


def test_cashtag_always_extracted():
    assert extract_tickers("I love $NVDA here", cfg()) == ["NVDA"]


def test_ambiguous_word_without_context_is_ignored():
    # "AI" and "NOW" are common words; no trading context -> not tickers
    assert extract_tickers("I will do it now, AI is everywhere", cfg()) == []


def test_ambiguous_word_with_context_is_accepted():
    out = extract_tickers("AI calls printing into earnings", cfg())
    assert "AI" in out


def test_option_cue_validates_ambiguous():
    out = extract_tickers("ON 25c looks juicy", cfg())
    assert "ON" in out


def test_hard_blocklist_never_ticker_even_with_cashtag():
    assert "USD" not in extract_tickers("$USD pair and $YOLO", cfg())
    assert "YOLO" not in extract_tickers("$USD pair and $YOLO", cfg())


def test_unambiguous_uppercase_accepted():
    assert "GME" in extract_tickers("GME breakout over 20d high", cfg())


def test_company_name_mapping():
    c = cfg(company_map={"nvidia": "NVDA"})
    assert "NVDA" in extract_tickers("nvidia is ripping", c)


def test_dedup_within_text():
    out = extract_tickers("$NVDA $NVDA NVDA calls", cfg())
    assert out.count("NVDA") == 1


def test_count_mentions_one_per_text():
    texts = ["$NVDA going up", "$NVDA again", "GME calls", "no ticker here"]
    counts = count_mentions(texts, cfg())
    assert counts["NVDA"] == 2
    assert counts["GME"] == 1
