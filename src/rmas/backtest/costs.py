"""Realistic transaction-cost model: spread, slippage, commissions, IV crush.

Costs are applied to *both* legs (entry + exit). For options we model paying a
fraction of the bid-ask away from mid plus per-contract commission, and an
optional IV-crush haircut when entering long premium near a catalyst.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostConfig:
    equity_commission_per_share: float = 0.0
    equity_slippage_bps: float = 8.0
    options_commission_per_contract: float = 0.65
    options_slippage_mid_pct: float = 0.25
    iv_crush_haircut_pct: float = 0.20

    @classmethod
    def from_cfg(cls, node) -> "CostConfig":
        g = node.get if hasattr(node, "get") else (lambda k, d: d)
        return cls(
            equity_commission_per_share=g("equity_commission_per_share", 0.0),
            equity_slippage_bps=g("equity_slippage_bps", 8.0),
            options_commission_per_contract=g("options_commission_per_contract", 0.65),
            options_slippage_mid_pct=g("options_slippage_mid_pct", 0.25),
            iv_crush_haircut_pct=g("iv_crush_haircut_pct", 0.20),
        )


def equity_roundtrip_cost(entry: float, exit_: float, shares: int, cfg: CostConfig) -> float:
    """Total $ cost for an equity round trip (both sides)."""
    slip = cfg.equity_slippage_bps / 10_000.0
    slip_cost = (entry + exit_) * shares * slip
    comm = 2 * shares * cfg.equity_commission_per_share
    return slip_cost + comm


def equity_net_return(entry: float, exit_: float, shares: int, cfg: CostConfig,
                      direction: str = "long") -> float:
    """Net fractional return after costs for an equity trade."""
    if entry <= 0 or shares <= 0:
        return 0.0
    gross = (exit_ - entry) if direction == "long" else (entry - exit_)
    gross_usd = gross * shares
    net_usd = gross_usd - equity_roundtrip_cost(entry, exit_, shares, cfg)
    return net_usd / (entry * shares)


def option_fill_price(mid: float, bid: float, ask: float, cfg: CostConfig,
                      side: str = "buy") -> float:
    """Model an option fill: pay a fraction of half-spread away from mid."""
    half_spread = max(0.0, (ask - bid) / 2.0)
    adjust = half_spread * cfg.options_slippage_mid_pct
    return mid + adjust if side == "buy" else mid - adjust


def option_net_return(entry_mid: float, exit_mid: float, *, bid: float, ask: float,
                      contracts: int, cfg: CostConfig, iv_crush: bool = False) -> float:
    """Net fractional return for a long-option trade including crush haircut."""
    if entry_mid <= 0 or contracts <= 0:
        return 0.0
    buy = option_fill_price(entry_mid, bid, ask, cfg, "buy")
    sell = option_fill_price(exit_mid, bid, ask, cfg, "sell")
    if iv_crush:
        sell *= (1.0 - cfg.iv_crush_haircut_pct)
    gross_usd = (sell - buy) * 100 * contracts
    comm = 2 * contracts * cfg.options_commission_per_contract
    net_usd = gross_usd - comm
    return net_usd / (buy * 100 * contracts)
