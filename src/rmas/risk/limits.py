"""Portfolio-level guardrails: daily loss, concurrency, sector/meme exposure."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PortfolioState:
    account_equity: float
    realized_pnl_today: float = 0.0
    open_trades: int = 0
    sector_exposure_usd: dict[str, float] = field(default_factory=dict)
    meme_exposure_usd: float = 0.0


@dataclass
class LimitDecision:
    allowed: bool
    blockers: list[str] = field(default_factory=list)
    scale: float = 1.0       # may shrink size to fit remaining sector room


def check_limits(
    state: PortfolioState,
    *,
    proposed_notional: float,
    sector: str,
    is_meme: bool,
    cfg,
) -> LimitDecision:
    """Apply all hard portfolio limits to a proposed new trade."""
    blockers: list[str] = []
    scale = 1.0
    eq = state.account_equity

    # Max daily loss.
    max_daily_loss = eq * (cfg.get("max_daily_loss_pct", 2.0) / 100.0)
    if state.realized_pnl_today <= -max_daily_loss:
        blockers.append("daily_loss_limit_hit")

    # Max concurrent trades.
    if state.open_trades >= cfg.get("max_concurrent_trades", 4):
        blockers.append("max_concurrent_trades")

    # Sector exposure.
    max_sector = eq * (cfg.get("max_sector_exposure_pct", 35) / 100.0)
    cur_sector = state.sector_exposure_usd.get(sector, 0.0)
    if cur_sector + proposed_notional > max_sector:
        room = max(0.0, max_sector - cur_sector)
        if room <= 0:
            blockers.append(f"sector_exposure_full:{sector}")
        else:
            scale = min(scale, room / proposed_notional)

    # Meme exposure.
    if is_meme:
        max_meme = eq * (cfg.get("max_meme_exposure_pct", 25) / 100.0)
        if state.meme_exposure_usd + proposed_notional > max_meme:
            room = max(0.0, max_meme - state.meme_exposure_usd)
            if room <= 0:
                blockers.append("meme_exposure_full")
            else:
                scale = min(scale, room / proposed_notional)

    return LimitDecision(allowed=(len(blockers) == 0), blockers=blockers, scale=round(scale, 4))
