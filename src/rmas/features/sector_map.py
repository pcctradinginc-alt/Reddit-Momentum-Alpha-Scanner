"""Map a Finnhub industry string to its SPDR sector ETF.

Finnhub's free profile2 exposes ``finnhubIndustry`` (GICS-flavoured labels,
not the 11 sectors), so we keyword-match. ORDER MATTERS: more specific
keywords first — "Internet & Direct Marketing Retail" must hit XLY (retail)
before the generic "internet" hits XLC.

Unknown industries return None; callers fall back to SPY.
"""

from __future__ import annotations

# (keyword, ETF) — matched lowercase, first hit wins.
_KEYWORD_ETF: list[tuple[str, str]] = [
    # consumer discretionary before generic tech/communication words
    ("retail", "XLY"),
    ("hotel", "XLY"), ("restaurant", "XLY"), ("leisure", "XLY"),
    ("apparel", "XLY"), ("luxury", "XLY"), ("textile", "XLY"),
    ("automobile", "XLY"), ("auto components", "XLY"),
    ("household durables", "XLY"), ("distributors", "XLY"),
    ("consumer services", "XLY"),
    # staples
    ("food", "XLP"), ("beverage", "XLP"), ("tobacco", "XLP"),
    ("household products", "XLP"), ("personal products", "XLP"),
    ("consumer products", "XLP"), ("staples", "XLP"),
    # health
    ("pharma", "XLV"), ("biotech", "XLV"), ("health", "XLV"),
    ("life sciences", "XLV"),
    # energy
    ("oil", "XLE"), ("gas", "XLE"), ("energy", "XLE"), ("coal", "XLE"),
    # financials
    ("bank", "XLF"), ("insurance", "XLF"), ("capital markets", "XLF"),
    ("financial", "XLF"), ("consumer finance", "XLF"),
    ("thrifts", "XLF"), ("mortgage finance", "XLF"),
    # real estate
    ("reit", "XLRE"), ("real estate", "XLRE"),
    # utilities
    ("utilit", "XLU"), ("renewable electricity", "XLU"), ("power producers", "XLU"),
    # materials
    ("chemical", "XLB"), ("metal", "XLB"), ("mining", "XLB"),
    ("paper", "XLB"), ("packaging", "XLB"), ("construction materials", "XLB"),
    ("containers", "XLB"),
    # industrials
    ("aerospace", "XLI"), ("defense", "XLI"), ("machinery", "XLI"),
    ("airline", "XLI"), ("air freight", "XLI"), ("road", "XLI"),
    ("rail", "XLI"), ("marine", "XLI"), ("logistics", "XLI"),
    ("transport", "XLI"), ("industrial", "XLI"), ("construction", "XLI"),
    ("building", "XLI"), ("electrical equipment", "XLI"),
    ("commercial services", "XLI"), ("professional services", "XLI"),
    ("trading companies", "XLI"),
    # technology
    ("semiconductor", "XLK"), ("software", "XLK"), ("technology", "XLK"),
    ("it services", "XLK"), ("computer", "XLK"), ("electronic", "XLK"),
    ("hardware", "XLK"), ("office electronics", "XLK"),
    # communication services
    ("media", "XLC"), ("telecom", "XLC"), ("communication", "XLC"),
    ("internet", "XLC"), ("entertainment", "XLC"), ("interactive", "XLC"),
]

SECTOR_ETFS = sorted({etf for _, etf in _KEYWORD_ETF})


def sector_etf(industry: str | None) -> str | None:
    """SPDR sector ETF for a Finnhub industry label, or None if unknown."""
    if not industry:
        return None
    label = industry.lower()
    for keyword, etf in _KEYWORD_ETF:
        if keyword in label:
            return etf
    return None
