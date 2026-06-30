"""Alpaca market-data adapter (daily bars + a liquidity snapshot).

Uses the Alpaca Data REST API via ``requests``. Falls back to synthetic data
offline or without keys. Keys come from the environment only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rmas.config import Secrets, is_offline
from rmas.data.base import SyntheticMarket
from rmas.logging_setup import get_logger
from rmas.types import Bar, LiquiditySnapshot

log = get_logger("data.alpaca")


class AlpacaAdapter:
    def __init__(self, secrets: Secrets | None = None, offline: bool | None = None):
        self.secrets = secrets or Secrets()
        self.offline = is_offline(self.secrets) if offline is None else offline
        self._synthetic = SyntheticMarket()

    @property
    def _live(self) -> bool:
        return not self.offline and self.secrets.alpaca_ready

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.secrets.get("ALPACA_API_KEY"),
            "APCA-API-SECRET-KEY": self.secrets.get("ALPACA_API_SECRET"),
        }

    def daily_bars(self, ticker: str, lookback_days: int = 60) -> list[Bar]:
        if not self._live:
            return self._synthetic.daily_bars(ticker, lookback_days)
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
            return out or self._synthetic.daily_bars(ticker, lookback_days)
        except Exception as exc:  # pragma: no cover
            log.warning("alpaca bars failed for %s (%s); synthetic", ticker, exc)
            return self._synthetic.daily_bars(ticker, lookback_days)

    def liquidity(self, ticker: str) -> LiquiditySnapshot:
        # A full liquidity snapshot needs fundamentals/short-interest from a
        # dedicated provider; for now derive volume-based fields from bars and
        # fall back to synthetic for the rest.
        bars = self.daily_bars(ticker, 5)
        if not bars:
            return self._synthetic.liquidity(ticker)
        last = bars[-1]
        synth = self._synthetic.liquidity(ticker)
        return LiquiditySnapshot(
            ticker=ticker,
            market_cap_usd=synth.market_cap_usd,
            dollar_volume_usd=last.close * last.volume,
            bid_ask_spread_bps=synth.bid_ask_spread_bps,
            short_interest_pct=synth.short_interest_pct,
            float_shares=synth.float_shares,
            borrow_available=synth.borrow_available,
        )
