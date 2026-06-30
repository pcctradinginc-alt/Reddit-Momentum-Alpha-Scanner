"""Paper broker — a deterministic, JSON-backed simulated blotter.

Records TradePlans as open positions, marks them against a price function, and
closes on stop/target/time-stop. Never connects to a real broker. (A live
Alpaca *paper* account can be wired later via the data adapters, but the
default is fully local so it runs without keys.)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rmas.config import ROOT
from rmas.logging_setup import get_logger
from rmas.types import TradePlan

log = get_logger("paper.broker")
BLOTTER = ROOT / "data" / "paper_blotter.json"


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


@dataclass
class Blotter:
    positions: list[Position] = field(default_factory=list)
    realized_pnl: float = 0.0

    @classmethod
    def load(cls, path: Path = BLOTTER) -> "Blotter":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
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

    def open_from_plan(self, plan: TradePlan) -> Position:
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
        )
        self.positions.append(pos)
        log.info("PAPER OPEN %s %s %d sh @ %.2f", pos.direction, pos.ticker, pos.shares, pos.entry)
        return pos

    def mark_and_close(self, ticker: str, price: float, reason: str = "manual") -> float:
        """Close any open position in ``ticker`` at ``price``; return P&L."""
        pnl = 0.0
        for p in self.open_positions:
            if p.ticker != ticker:
                continue
            gross = (price - p.entry) if p.direction == "long" else (p.entry - price)
            p.pnl_usd = round(gross * p.shares, 2)
            p.exit = round(price, 4)
            p.closed_at = datetime.now(timezone.utc).isoformat()
            p.status = "closed"
            p.exit_reason = reason
            self.realized_pnl += p.pnl_usd
            pnl += p.pnl_usd
            log.info("PAPER CLOSE %s @ %.2f pnl=%.2f (%s)", p.ticker, price, p.pnl_usd, reason)
        return pnl
