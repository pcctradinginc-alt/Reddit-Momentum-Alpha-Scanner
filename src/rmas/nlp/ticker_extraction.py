"""Ticker extraction with disambiguation.

The hard problem: many tickers are also common English words (AI, ON, NOW,
CAT, DD, ARE, FOR, ALL ...). Counting every uppercase token as a ticker
floods the scanner with noise. Rules, in order of confidence:

  1. Cashtag ``$AAPL``               -> always accepted (unless hard-blocklisted).
  2. Known company name in text       -> accepted, mapped to its symbol.
  3. Bare uppercase token             -> accepted ONLY if unambiguous, OR
                                         ambiguous-but-context-confirmed.
  4. hard_blocklist (USD, CPI, WSB..) -> never a ticker.

Context confirmation = a trading cue ("calls", "earnings", "squeeze", ...) or
an option-chain pattern ("$5 calls", "250c", "Jun 21 puts") near the symbol.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

# A cashtag: $ followed by 1-5 letters (optionally a class suffix like BRK.B).
_CASHTAG = re.compile(r"\$([A-Za-z]{1,5})(?:\.[A-Za-z])?\b")
# Bare uppercase candidate token (1-5 letters), word-bounded.
_BARE = re.compile(r"\b([A-Z]{1,5})\b")
# Option-chain-ish patterns that strongly imply the preceding token is a ticker.
_OPTION_CUE = re.compile(
    r"\b\d{1,5}(?:\.\d+)?\s*[cp]\b"           # 250c / 7.5p
    r"|\b\d{1,4}\s*(?:call|put)s?\b"          # 5 calls
    r"|\b(?:call|put)s?\b"                    # calls / puts
    r"|\b\d{1,2}/\d{1,2}\b",                  # 6/21 expiry
    re.IGNORECASE,
)


@dataclass
class ExtractionConfig:
    ambiguous_symbols: set[str]
    hard_blocklist: set[str]
    context_cues: set[str]
    company_map: dict[str, str]               # lowercase company name -> symbol
    min_symbol_len: int = 1
    max_symbol_len: int = 5

    @classmethod
    def from_universe(cls, universe: dict, company_map: dict[str, str] | None = None):
        te = universe.get("ticker_extraction", universe) if universe else {}
        return cls(
            ambiguous_symbols={s.upper() for s in te.get("ambiguous_symbols", [])},
            hard_blocklist={s.upper() for s in te.get("hard_blocklist", [])},
            context_cues={c.lower() for c in te.get("context_cues", [])},
            company_map={k.lower(): v.upper() for k, v in (company_map or {}).items()},
            min_symbol_len=int(te.get("min_symbol_len", 1)),
            max_symbol_len=int(te.get("max_symbol_len", 5)),
        )


def _has_context(text_lower: str, cues: set[str]) -> bool:
    if any(cue in text_lower for cue in cues):
        return True
    return bool(_OPTION_CUE.search(text_lower))


def _valid_symbol(sym: str, cfg: ExtractionConfig) -> bool:
    return cfg.min_symbol_len <= len(sym) <= cfg.max_symbol_len


def extract_tickers(text: str, cfg: ExtractionConfig) -> list[str]:
    """Return the list of confidently-identified ticker symbols in ``text``.

    Duplicates within the same text are collapsed (one mention per ticker/text),
    which avoids a single spammy post inflating counts.
    """
    if not text:
        return []
    text_lower = text.lower()
    found: set[str] = set()

    # 1) Cashtags — highest confidence.
    for m in _CASHTAG.finditer(text):
        sym = m.group(1).upper()
        if sym in cfg.hard_blocklist or not _valid_symbol(sym, cfg):
            continue
        found.add(sym)

    # 2) Company-name mentions.
    for name, sym in cfg.company_map.items():
        if name and name in text_lower and sym not in cfg.hard_blocklist:
            found.add(sym)

    # 3) Bare uppercase tokens — disambiguate.
    has_ctx = _has_context(text_lower, cfg.context_cues)
    for m in _BARE.finditer(text):
        sym = m.group(1)
        if sym in cfg.hard_blocklist or not _valid_symbol(sym, cfg):
            continue
        if sym in found:
            continue
        # Single-letter tokens (I, A, ...) and configured ambiguous words are
        # high-ambiguity: accept only when trading context confirms them.
        if sym in cfg.ambiguous_symbols or len(sym) == 1:
            if has_ctx:
                found.add(sym)
        else:
            # Unambiguous uppercase token (e.g. NVDA, GME) — accept.
            found.add(sym)

    return sorted(found)


def count_mentions(texts: list[str], cfg: ExtractionConfig) -> Counter:
    """Count ticker mentions across many texts (one per ticker per text)."""
    counter: Counter = Counter()
    for t in texts:
        for sym in extract_tickers(t, cfg):
            counter[sym] += 1
    return counter
