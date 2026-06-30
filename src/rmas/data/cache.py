"""Tiny JSON file cache for adapter responses (rate-limit friendliness).

Point-in-time integrity: cache keys include the date so a day's snapshot is
frozen once written, which helps reproduce backtests/alerts deterministically.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from rmas.config import ROOT

CACHE_DIR = ROOT / "data" / "cache"


def _path(namespace: str, key: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
    d = CACHE_DIR / namespace
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe}.json"


def get(namespace: str, key: str, max_age_s: float | None = None) -> Any | None:
    p = _path(namespace, key)
    if not p.exists():
        return None
    if max_age_s is not None and (time.time() - p.stat().st_mtime) > max_age_s:
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def put(namespace: str, key: str, value: Any) -> None:
    p = _path(namespace, key)
    try:
        p.write_text(json.dumps(value, default=str))
    except Exception:
        pass
