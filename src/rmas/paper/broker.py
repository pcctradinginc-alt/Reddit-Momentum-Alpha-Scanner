"""Paper broker — a deterministic, JSON-backed simulated blotter.

Records TradePlans as open positions, marks them daily against real bars and
closes on stop / target / time-stop. Never connects to a real broker.

This is the system's FEEDBACK LOOP: every closed position lands in
``data/state/outcomes.json`` with its R-multiple, max-favorable/max-adverse
excursion and the full feature snapshot from entry day — the training data
the (currently inactive) meta-labeling gate needs to become real. State lives
under data/state/ so the CI actions/cache persists it between runs.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rmas.config import ROOT
from rmas.logging_setup import get_logger
from rmas.types import Bar, TradePlan

log = get_logger("paper.broker")
BLOTTER = ROOT / "data" / "state" / "paper_blotter.json"
LEGACY_BLOTTER = ROOT / "data" / "paper_blotter.json"
OUTCOMES = ROOT / "data" / "state" / "outcomes.json"


@dataclass
class Position:
    ticker: str
    strategy: str
    direction: str
    entry: float
    stop: float
    targets: list[float]
    shares: int
    opened_at: str
    time_stop_days: int
    status: str = "open"            # open / closed
    exit: float | None = None
    closed_at: str | None = None
    pnl_usd: float = 0.0
    exit_reason: str = ""
    # feedback-loop fields
    features: dict = field(default_factory=dict)
    mfe: float = 0.0                # best price seen while open
    mae: float = 0.0                # worst price seen while open
    r_multiple: float = 0.0         # realized P&L per share / initial risk


@dataclass
class Blotter:
    positions: list[Position] = field(default_factory=list)
    realized_pnl: float = 0.0

    @classmethod
    def load(cls, path: Path = BLOTTER) -> "Blotter":
        src = path
        if path == BLOTTER and not path.exists() and LEGACY_BLOTTER.exists():
            src = LEGACY_BLOTTER
        if not src.exists():
            return cls()
        raw = json.loads(src.read_text())
        positions = [Position(**p) for p in raw.get("positions", [])]
        return cls(positions=positions, realized_pnl=raw.get("realized_pnl", 0.0))

    def save(self, path: Path = BLOTTER) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"positions": [asdict(p) for p in self.positions],
             "realized_pnl": round(self.realized_pnl, 2)},
            indent=2,
        ))

    @property
    def open_positions(self) -> list[Position]:
        return [p for p in self.positions if p.status == "open"]

    def open_from_plan(self, plan: TradePlan) -> Position | None:
        if any(p.ticker == plan.ticker for p in self.open_positions):
            log.info("PAPER SKIP %s — already open", plan.ticker)
            return None
        pos = Position(
            ticker=plan.ticker,
            strategy=plan.strategy,
            direction=plan.direction,
            entry=plan.entry,
            stop=plan.stop,
            targets=plan.targets,
            shares=plan.shares,
            opened_at=datetime.now(timezone.utc).isoformat(),
            time_stop_days=plan.time_stop_days,
            features=dict(getattr(plan, "features", {}) or {}),
            mfe=plan.entry,
            mae=plan.entry,
        )
        self.positions.append(pos)
        log.info("PAPER OPEN %s %s %d sh @ %.2f", pos.direction, pos.ticker, pos.shares, pos.entry)
        return pos

    # ------------------------------------------------------------------ #
    def update_open(self, bars_fn, asof: datetime | None = None) -> int:
        """Mark every open position against daily bars from
        ``bars_fn(ticker, lookback_days) -> list[Bar]`` and close on
        stop -> target -> time-stop (checked in that conservative order per
        bar). Returns the number of positions closed."""
        asof = asof or datetime.now(timezone.utc)
        closed = 0
        for p in list(self.open_positions):
            bars: list[Bar] = bars_fn(p.ticker, p.time_stop_days + 15) or []
            opened = datetime.fromisoformat(p.opened_at)
            day0 = opened.replace(hour=0, minute=0, second=0, microsecond=0)
            bars = [b for b in bars if b.t >= day0]
            if not bars:
                continue
            if self._walk_bars(p, bars, asof):
                closed += 1
        if closed:
            self.save()
        return closed

    def _walk_bars(self, p: Position, bars: list[Bar], asof: datetime) -> bool:
        long = p.direction == "long"
        target = p.targets[-1] if p.targets else None
        for b in bars:
            if long:
                p.mfe = max(p.mfe, b.high)
                p.mae = min(p.mae, b.low)
            else:
                p.mfe = min(p.mfe, b.low)
                p.mae = max(p.mae, b.high)
            if (b.low <= p.stop) if long else (b.high >= p.stop):
                self._close(p, p.stop, "stop", b.t)
                return True
            if target is not None and ((b.high >= target) if long else (b.low <= target)):
                self._close(p, target, "target", b.t)
                return True
        opened = datetime.fromisoformat(p.opened_at)
        if (asof - opened).days >= p.time_stop_days:
            self._close(p, bars[-1].close, "time_stop", bars[-1].t)
            return True
        return False

    def _close(self, p: Position, price: float, reason: str, when: datetime) -> None:
        sign = 1.0 if p.direction == "long" else -1.0
        per_share = sign * (price - p.entry)
        risk = abs(p.entry - p.stop) or 1e-9
        p.exit = round(price, 4)
        p.closed_at = when.isoformat()
        p.status = "closed"
        p.exit_reason = reason
        p.pnl_usd = round(per_share * p.shares, 2)
        p.r_multiple = round(per_share / risk, 3)
        self.realized_pnl += p.pnl_usd
        _append_outcome(p)
        log.info("PAPER CLOSE %s @ %.2f pnl=%.2f R=%.2f (%s)",
                 p.ticker, price, p.pnl_usd, p.r_multiple, reason)

    def mark_and_close(self, ticker: str, price: float, reason: str = "manual") -> float:
        """Close any open position in ``ticker`` at ``price``; return P&L."""
        pnl = 0.0
        for p in self.open_positions:
            if p.ticker != ticker:
                continue
            self._close(p, price, reason, datetime.now(timezone.utc))
            pnl += p.pnl_usd
        return pnl

    def summary(self) -> str:
        closed = [p for p in self.positions if p.status == "closed"]
        wins = [p for p in closed if p.r_multiple > 0]
        avg_r = sum(p.r_multiple for p in closed) / len(closed) if closed else 0.0
        return (f"paper: open={len(self.open_positions)} closed={len(closed)} "
                f"wins={len(wins)} avgR={avg_r:+.2f} pnl=${self.realized_pnl:,.0f}")


def _append_outcome(p: Position) -> None:
    """Append the closed trade to the meta-label training log."""
    risk = abs(p.entry - p.stop) or 1e-9
    sign = 1.0 if p.direction == "long" else -1.0
    rec = {
        "ticker": p.ticker, "strategy": p.strategy, "direction": p.direction,
        "opened_at": p.opened_at, "closed_at": p.closed_at,
        "entry": p.entry, "exit": p.exit, "exit_reason": p.exit_reason,
        "r_multiple": p.r_multiple,
        "mfe_r": round(sign * (p.mfe - p.entry) / risk, 3) if p.mfe else 0.0,
        "mae_r": round(sign * (p.mae - p.entry) / risk, 3) if p.mae else 0.0,
        "win": p.r_multiple > 0,
        "features": p.features,
    }
    try:
        OUTCOMES.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(OUTCOMES.read_text()) if OUTCOMES.exists() else []
        data.append(rec)
        OUTCOMES.write_text(json.dumps(data, indent=1))
    except Exception as exc:
        log.warning("outcome log failed (%s)", exc)
