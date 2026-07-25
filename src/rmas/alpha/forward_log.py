"""Forward-return logging: did the raw SIGNAL work, independent of our exit rules?

The paper blotter (``rmas.paper.broker``) measures whether our simulated
stop/target/time-stop exit made money — that conflates signal quality with
exit-rule quality. This module logs, for every alerted setup, the reference
entry price and then backfills REAL forward returns at fixed horizons
(1/3/5/10 trading days by default) with no exit logic at all. That is the
purest measure of "was this a good ticker to have flagged."

Records are appended to a JSONL file (one alert per line) so a partial write
never corrupts prior history. ``backfill`` is idempotent and lookahead-safe:
a horizon is only ever filled once a bar actually exists at that offset from
the entry session; otherwise it stays ``null`` and the record stays
incomplete forever if that day still hasn't happened yet.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from rmas.config import ROOT
from rmas.logging_setup import get_logger

log = get_logger("alpha.forward_log")

FORWARD_LOG = ROOT / "data" / "state" / "forward_log.jsonl"

DEFAULT_HORIZONS = [1, 3, 5, 10]


def _as_date(d) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d)[:10])


def _read_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            log.warning("forward-log: skipping unparsable line")
    return out


def _write_records(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    body = "\n".join(json.dumps(r) for r in records)
    tmp.write_text(body + ("\n" if records else ""))
    tmp.replace(path)


# --------------------------------------------------------------------------- #
def log_scan(res, horizons: list[int] | None = None, path: Path = FORWARD_LOG) -> int:
    """Append one forward-log record per alerted plan in ``res``.

    Idempotent per day: a record with the same ``"<date>_<ticker>"`` id is
    never duplicated. Never raises — a broken forward-log must not sink a
    scan/alert run.
    """
    horizons = horizons or DEFAULT_HORIZONS
    try:
        existing = _read_records(path)
        existing_ids = {r.get("id") for r in existing}

        date_str = _as_date(res.asof).isoformat()
        cand_by_ticker = {c.ticker: c for c in res.candidates}
        regime_str = ""
        if getattr(res, "regime", None) is not None:
            r = res.regime.regime
            regime_str = getattr(r, "value", None) or str(r)

        new_records: list[dict] = []
        for plan in res.plans:
            rec_id = f"{date_str}_{plan.ticker}"
            if rec_id in existing_ids:
                continue
            cand = cand_by_ticker.get(plan.ticker)
            rec = {
                "id": rec_id,
                "date": date_str,
                "ticker": plan.ticker,
                "strategy": plan.strategy,
                "regime": regime_str,
                "entry_ref": plan.entry,
                "rank_score": getattr(cand, "rank_score", 0.0) if cand else 0.0,
                "meta_probability": getattr(cand, "meta_probability", None) if cand else None,
                "discovery": cand.discovery.score if cand and cand.discovery else 0.0,
                "tradeability": cand.tradeability.score if cand and cand.tradeability else 0.0,
                "timing_risk": cand.timing_risk.score if cand and cand.timing_risk else 0.0,
                "features": dict(cand.features) if cand else dict(getattr(plan, "features", {}) or {}),
                "returns": {str(h): None for h in horizons},
                "complete": False,
            }
            new_records.append(rec)
            existing_ids.add(rec_id)

        if new_records:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as f:
                for rec in new_records:
                    f.write(json.dumps(rec) + "\n")
        return len(new_records)
    except Exception as exc:
        log.warning("forward-log scan logging failed (%s)", exc)
        return 0


# --------------------------------------------------------------------------- #
def backfill(bars_fn, asof: date | datetime | None = None,
            horizons: list[int] | None = None, path: Path = FORWARD_LOG) -> int:
    """Fill in matured horizon returns for every incomplete record.

    ``bars_fn(ticker, lookback_days) -> list[Bar]``. For each incomplete
    record: find the first bar at/after the record's date (the entry
    session, index i0), then for every horizon ``h`` still null, fill it
    ONLY if a bar exists at ``i0 + h`` — never fabricated, never looked
    ahead. Rewrites the JSONL file atomically. Never raises.
    """
    horizons = horizons or DEFAULT_HORIZONS
    asof_date = _as_date(asof) if asof is not None else date.today()

    try:
        records = _read_records(path)
    except Exception as exc:
        log.warning("forward-log read failed (%s)", exc)
        return 0
    if not records:
        return 0

    changed = 0
    for rec in records:
        if rec.get("complete"):
            continue
        try:
            entry_date = date.fromisoformat(rec["date"])
        except Exception:
            continue

        lookback = max((asof_date - entry_date).days + max(horizons) + 10, 30)
        try:
            bars = bars_fn(rec["ticker"], lookback) or []
        except Exception as exc:
            log.warning("forward-log bars fetch failed for %s (%s)", rec.get("ticker"), exc)
            continue
        if not bars:
            continue

        i0 = None
        for i, b in enumerate(bars):
            bd = b.t.date() if hasattr(b.t, "date") else b.t
            if bd >= entry_date:
                i0 = i
                break
        if i0 is None:
            continue

        entry_ref = rec.get("entry_ref") or 0.0
        returns = rec.setdefault("returns", {})
        rec_changed = False
        for h in horizons:
            key = str(h)
            if key not in returns or returns[key] is not None:
                continue
            idx = i0 + h
            if idx < len(bars) and entry_ref:
                returns[key] = round((bars[idx].close - entry_ref) / entry_ref, 4)
                rec_changed = True
            # else: not enough elapsed trading days yet -> stays null, no lookahead

        if returns and all(v is not None for v in returns.values()):
            rec["complete"] = True
        if rec_changed:
            changed += 1

    if changed:
        try:
            _write_records(path, records)
        except Exception as exc:
            log.warning("forward-log rewrite failed (%s)", exc)
    return changed


# --------------------------------------------------------------------------- #
def report(horizons: list[int] | None = None, path: Path = FORWARD_LOG) -> str:
    """Human-readable summary of forward-return quality over complete records."""
    horizons = horizons or DEFAULT_HORIZONS
    try:
        records = _read_records(path)
    except Exception as exc:
        return f"forward-log: unreadable ({exc})"

    if not records:
        return "forward-log: no records yet."

    complete = [r for r in records if r.get("complete")]
    maturing = len(records) - len(complete)
    if not complete:
        return f"forward-log: no complete records yet ({maturing} maturing, {len(records)} total)."

    out = [f"Forward-log report — {len(complete)} complete record(s), {maturing} still maturing",
          "-" * 60]
    for h in horizons:
        key = str(h)
        vals = [r["returns"][key] for r in complete
               if r.get("returns", {}).get(key) is not None]
        if not vals:
            continue
        n = len(vals)
        mean_ret = sum(vals) / n
        hit = sum(1 for v in vals if v > 0) / n
        out.append(f"  {h:>3}d: n={n:<4} mean_return={mean_ret:+.4f} hit_rate={hit:.0%}")

    longest = max(horizons)
    lkey = str(longest)

    def _grouped(field: str) -> dict[str, list[float]]:
        groups: dict[str, list[float]] = {}
        for r in complete:
            v = r.get("returns", {}).get(lkey)
            if v is None:
                continue
            groups.setdefault(r.get(field) or "?", []).append(v)
        return groups

    by_strat = _grouped("strategy")
    if by_strat:
        out.append(f"\nPer-strategy mean {longest}d return:")
        for strat, vals in sorted(by_strat.items()):
            out.append(f"  {strat:<28} n={len(vals):<4} mean={sum(vals) / len(vals):+.4f}")

    by_regime = _grouped("regime")
    if by_regime:
        out.append(f"\nPer-regime mean {longest}d return:")
        for reg, vals in sorted(by_regime.items()):
            out.append(f"  {reg:<28} n={len(vals):<4} mean={sum(vals) / len(vals):+.4f}")

    return "\n".join(out)
