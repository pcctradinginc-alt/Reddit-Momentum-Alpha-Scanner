"""Alpaca market-data adapter (daily bars, benchmark returns, liquidity).

Uses the Alpaca Data REST API via ``requests``. Falls back to synthetic data
offline or without keys. Keys come from the environment only.

Cost discipline: live bar responses are cached per (ticker, lookback, day) in
the local JSON cache, so re-runs and the multiple consumers within one scan
never pay for the same request twice.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rmas.config import Secrets, is_offline
from rmas.data import cache
from rmas.data.base import SyntheticMarket
from rmas.logging_setup import get_logger
from rmas.mathx import pct_change
from rmas.types import Bar, LiquiditySnapshot

log = get_logger("data.alpaca")

# Offline defaults preserve the deterministic synthetic behaviour.
_OFFLINE_BENCHMARKS = {"SPY": 0.01, "QQQ": 0.012, "SECTOR": 0.008}


class AlpacaAdapter:
    def __init__(self, secrets: Secrets | None = None, offline: bool | None = None):
        self.secrets = secrets or Secrets()
        self.offline = is_offline(self.secrets) if offline is None else offline
        self._synthetic = SyntheticMarket()

    @property
    def _live(self) -> bool:
        return not self.offline and self.secrets.alpaca_ready

    @property
    def mode(self) -> str:
        """"live" when real market data is in play, else "synthetic"."""
        return "live" if self._live else "synthetic"

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.secrets.get("ALPACA_API_KEY"),
            "APCA-API-SECRET-KEY": self.secrets.get("ALPACA_API_SECRET"),
        }

    # ------------------------------------------------------------------ #
    def daily_bars(self, ticker: str, lookback_days: int = 60) -> list[Bar]:
        if not self._live:
            return self._synthetic.daily_bars(ticker, lookback_days)

        day = datetime.now(timezone.utc).date().isoformat()
        cache_key = f"{ticker}_{lookback_days}_{day}"
        cached = cache.get("alpaca_bars", cache_key)
        if cached:
            return [Bar(t=datetime.fromisoformat(b["t"]), open=b["o"], high=b["h"],
                        low=b["l"], close=b["c"], volume=b["v"]) for b in cached]

        try:  # pragma: no cover - live network
            import requests

            data_url = self.secrets.get("ALPACA_DATA_URL", "https://data.alpaca.markets")
            start = (datetime.now(timezone.utc) - timedelta(days=lookback_days * 2)).date().isoformat()
            url = f"{data_url}/v2/stocks/{ticker}/bars"
            params = {"timeframe": "1Day", "start": start, "limit": lookback_days, "adjustment": "split"}
            r = requests.get(url, headers=self._headers(), params=params, timeout=15)
            r.raise_for_status()
            bars = r.json().get("bars", [])
            out = [
                Bar(
                    t=datetime.fromisoformat(b["t"].replace("Z", "+00:00")),
                    open=b["o"], high=b["h"], low=b["l"], close=b["c"], volume=b["v"],
                )
                for b in bars
            ]
            if out:
                cache.put("alpaca_bars", cache_key,
                          [{"t": b.t.isoformat(), "o": b.open, "h": b.high,
                            "l": b.low, "c": b.close, "v": b.volume} for b in out])
            return out or self._synthetic.daily_bars(ticker, lookback_days)
        except Exception as exc:  # pragma: no cover
            log.warning("alpaca bars failed for %s (%s); synthetic", ticker, exc)
            return self._synthetic.daily_bars(ticker, lookback_days)

    # ------------------------------------------------------------------ #
    def benchmark_returns(self, lookback: int = 20) -> dict[str, float]:
        """Real SPY/QQQ `lookback`-day returns (live) or neutral defaults."""
        if not self._live:
            return dict(_OFFLINE_BENCHMARKS)
        out: dict[str, float] = {}
        for sym in ("SPY", "QQQ"):
            bars = self.daily_bars(sym, lookback + 5)
            if len(bars) > lookback:
                out[sym] = pct_change(bars[-1 - lookback].close, bars[-1].close)
            else:
                out[sym] = 0.0
        out["SECTOR"] = out.get("SPY", 0.0)  # sector proxy until sector ETFs are wired
        return out

    # ------------------------------------------------------------------ #
    def spread_bps(self, ticker: str) -> float | None:
        """Bid/ask spread in bps from the latest quote (live only)."""
        if not self._live:
            return None
        try:  # pragma: no cover - live network
            import requests

            data_url = self.secrets.get("ALPACA_DATA_URL", "https://data.alpaca.markets")
            r = requests.get(f"{data_url}/v2/stocks/{ticker}/quotes/latest",
                             headers=self._headers(), timeout=15)
            r.raise_for_status()
            q = r.json().get("quote", {})
            bid, ask = float(q.get("bp", 0)), float(q.get("ap", 0))
            if bid <= 0 or ask <= 0 or ask < bid:
                return None
            mid = (bid + ask) / 2.0
            return (ask - bid) / mid * 10_000.0
        except Exception as exc:  # pragma: no cover
            log.warning("alpaca quote failed for %s (%s)", ticker, exc)
            return None

    # ------------------------------------------------------------------ #
    def liquidity(self, ticker: str, bars: list[Bar] | None = None,
                  market_cap_usd: float | None = None) -> LiquiditySnapshot:
        """Liquidity snapshot. Pass `bars` to reuse already-fetched data and
        `market_cap_usd` from a fundamentals source; missing pieces fall back
        to synthetic values."""
        bars = bars or self.daily_bars(ticker, 5)
        if not bars:
            return self._synthetic.liquidity(ticker)
        last = bars[-1]
        synth = self._synthetic.liquidity(ticker)
        spread = self.spread_bps(ticker)
        return LiquiditySnapshot(
            ticker=ticker,
            market_cap_usd=market_cap_usd if market_cap_usd else synth.market_cap_usd,
            dollar_volume_usd=last.close * last.volume,
            bid_ask_spread_bps=spread if spread is not None else synth.bid_ask_spread_bps,
            short_interest_pct=synth.short_interest_pct,
            float_shares=synth.float_shares,
            borrow_available=synth.borrow_available,
        )
