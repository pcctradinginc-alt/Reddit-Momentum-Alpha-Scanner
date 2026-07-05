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
        # Which data feed actually served bars: "sip" (consolidated, correct
        # volume) or "iex" (single venue, ~2-3% of market volume). Logged once.
        self.feed_used: str | None = None

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
            url = f"{data_url}/v2/stocks/{ticker}/bars"
            # End the window >15 min in the past: the free (Basic) plan may
            # query full consolidated SIP history outside that window. IEX is
            # only a fallback — its volume is a ~2-3% sliver of the market and
            # would poison rel_volume and the dollar-volume liquidity filter.
            end = datetime.now(timezone.utc) - timedelta(minutes=16)
            start = (end - timedelta(days=lookback_days * 2)).date().isoformat()
            params = {"timeframe": "1Day", "start": start,
                      "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "limit": lookback_days, "adjustment": "split"}

            out: list[Bar] = []
            for feed in ("sip", "iex"):
                r = requests.get(url, headers=self._headers(),
                                 params={**params, "feed": feed}, timeout=15)
                if r.status_code != 200:
                    log.debug("alpaca bars feed=%s -> %s for %s", feed, r.status_code, ticker)
                    continue
                bars = r.json().get("bars", [])
                out = [
                    Bar(
                        t=datetime.fromisoformat(b["t"].replace("Z", "+00:00")),
                        open=b["o"], high=b["h"], low=b["l"], close=b["c"], volume=b["v"],
                    )
                    for b in bars
                ]
                if out:
                    if self.feed_used != feed:
                        self.feed_used = feed
                        log.info("alpaca live bars: feed=%s%s", feed,
                                 "" if feed == "sip" else
                                 " (WARNING: IEX volume is a small market sliver)")
                    break

            if out:
                cache.put("alpaca_bars", cache_key,
                          [{"t": b.t.isoformat(), "o": b.open, "h": b.high,
                            "l": b.low, "c": b.close, "v": b.volume} for b in out])
            return out or self._synthetic.daily_bars(ticker, lookback_days)
        except Exception as exc:  # pragma: no cover
            log.warning("alpaca bars failed for %s (%s); synthetic", ticker, exc)
            return self._synthetic.daily_bars(ticker, lookback_days)

    # ------------------------------------------------------------------ #
    def symbol_return(self, symbol: str, lookback: int = 20) -> float:
        """`lookback`-day return for one symbol from (cached) daily bars."""
        bars = self.daily_bars(symbol, lookback + 5)
        if len(bars) > lookback:
            return pct_change(bars[-1 - lookback].close, bars[-1].close)
        return 0.0

    def benchmark_returns(self, lookback: int = 20) -> dict[str, float]:
        """Real SPY/QQQ `lookback`-day returns (live) or neutral defaults."""
        if not self._live:
            return dict(_OFFLINE_BENCHMARKS)
        out = {sym: self.symbol_return(sym, lookback) for sym in ("SPY", "QQQ")}
        out["SECTOR"] = out.get("SPY", 0.0)  # per-ticker sector ETF overrides this
        return out

    # ------------------------------------------------------------------ #
    def premarket_volumes(self, ticker: str) -> tuple[float, float] | None:
        """(today's premarket volume, avg over previous sessions) — or None.

        Like-for-like: previous sessions are summed over the SAME
        time-of-day window as today (04:00 ET .. now-16min, capped 09:30),
        so a pre-market scan never compares a partial today against full
        historical premarkets. One minute-bars request per ticker, live only.
        """
        if not self._live:
            return None
        try:  # pragma: no cover - live network
            from zoneinfo import ZoneInfo

            import requests

            ny = ZoneInfo("America/New_York")
            now_et = (datetime.now(timezone.utc) - timedelta(minutes=16)).astimezone(ny)
            open_t, pm_start = (9, 30), (4, 0)
            cutoff_tod = min((now_et.hour, now_et.minute), open_t)
            if now_et.weekday() >= 5 or cutoff_tod <= pm_start:
                return None                     # weekend / before premarket

            data_url = self.secrets.get("ALPACA_DATA_URL", "https://data.alpaca.markets")
            start = (now_et - timedelta(days=8)).date().isoformat()
            end = (datetime.now(timezone.utc) - timedelta(minutes=16)).strftime("%Y-%m-%dT%H:%M:%SZ")
            sums: dict[str, float] = {}
            for feed in ("sip", "iex"):
                r = requests.get(
                    f"{data_url}/v2/stocks/{ticker}/bars",
                    headers=self._headers(),
                    params={"timeframe": "1Min", "start": start, "end": end,
                            "limit": 10000, "adjustment": "raw", "feed": feed},
                    timeout=20,
                )
                if r.status_code != 200:
                    continue
                for b in r.json().get("bars", []):
                    t = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ny)
                    tod = (t.hour, t.minute)
                    if pm_start <= tod < cutoff_tod:
                        key = t.date().isoformat()
                        sums[key] = sums.get(key, 0.0) + float(b.get("v", 0))
                break
            if not sums:
                return None
            today_key = now_et.date().isoformat()
            today_pm = sums.pop(today_key, 0.0)
            prev = [sums[k] for k in sorted(sums)][-5:]
            if not prev:
                return None
            return today_pm, sum(prev) / len(prev)
        except Exception as exc:  # pragma: no cover
            log.warning("alpaca premarket volume failed for %s (%s)", ticker, exc)
            return None

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
                  market_cap_usd: float | None = None,
                  dollar_volume_usd: float | None = None,
                  float_shares: float | None = None) -> LiquiditySnapshot:
        """Liquidity snapshot. Pass `bars` to reuse already-fetched data and
        fundamentals from a real source (consolidated volume beats IEX-sliver
        bar volume).

        Honesty rule: in LIVE mode, fields we have no real source for are
        neutral (short_interest=0, borrow=True) — NEVER synthetic randoms
        that downstream strategies could mistake for data. Synthetic values
        exist only for offline demos/tests."""
        bars = bars or self.daily_bars(ticker, 5)
        if not bars:
            return self._synthetic.liquidity(ticker)
        last = bars[-1]
        spread = self.spread_bps(ticker)
        if self._live:
            return LiquiditySnapshot(
                ticker=ticker,
                market_cap_usd=market_cap_usd or 0.0,
                dollar_volume_usd=dollar_volume_usd or last.close * last.volume,
                bid_ask_spread_bps=spread if spread is not None else 0.0,
                short_interest_pct=0.0,          # no real source yet
                float_shares=float_shares or 0.0,  # shares outstanding (upper bound)
                borrow_available=True,           # unknown -> neutral
            )
        synth = self._synthetic.liquidity(ticker)
        return LiquiditySnapshot(
            ticker=ticker,
            market_cap_usd=market_cap_usd if market_cap_usd else synth.market_cap_usd,
            dollar_volume_usd=dollar_volume_usd if dollar_volume_usd else last.close * last.volume,
            bid_ask_spread_bps=spread if spread is not None else synth.bid_ask_spread_bps,
            short_interest_pct=synth.short_interest_pct,
            float_shares=synth.float_shares,
            borrow_available=synth.borrow_available,
        )
