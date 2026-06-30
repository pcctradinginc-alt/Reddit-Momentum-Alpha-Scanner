"""Tradier options adapter -> aggregated OptionsSnapshot.

Pulls the option chain, aggregates call/put volume & OI, estimates ATM spread
and a crude IV rank. Falls back to synthetic offline / without a key.
"""

from __future__ import annotations

from datetime import datetime, timezone

from rmas.config import Secrets, is_offline
from rmas.data.base import SyntheticOptions
from rmas.logging_setup import get_logger
from rmas.types import OptionsSnapshot

log = get_logger("data.tradier")


class TradierAdapter:
    def __init__(self, secrets: Secrets | None = None, offline: bool | None = None):
        self.secrets = secrets or Secrets()
        self.offline = is_offline(self.secrets) if offline is None else offline
        self._synthetic = SyntheticOptions()

    @property
    def _live(self) -> bool:
        return not self.offline and self.secrets.tradier_ready

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.secrets.get('TRADIER_API_KEY')}",
            "Accept": "application/json",
        }

    def options_snapshot(self, ticker: str) -> OptionsSnapshot:
        if not self._live:
            return self._synthetic.options_snapshot(ticker)
        try:  # pragma: no cover - live network
            import requests

            base = self.secrets.get("TRADIER_BASE_URL", "https://api.tradier.com")
            # nearest expiration
            exp_url = f"{base}/v1/markets/options/expirations"
            er = requests.get(exp_url, headers=self._headers(),
                              params={"symbol": ticker}, timeout=15)
            er.raise_for_status()
            exps = (er.json().get("expirations") or {}).get("date") or []
            if not exps:
                return self._synthetic.options_snapshot(ticker)
            expiry = exps[0] if isinstance(exps, list) else exps

            ch_url = f"{base}/v1/markets/options/chains"
            cr = requests.get(ch_url, headers=self._headers(),
                              params={"symbol": ticker, "expiration": expiry, "greeks": "true"},
                              timeout=20)
            cr.raise_for_status()
            options = (cr.json().get("options") or {}).get("option") or []

            call_v = sum(o.get("volume", 0) or 0 for o in options if o.get("option_type") == "call")
            put_v = sum(o.get("volume", 0) or 0 for o in options if o.get("option_type") == "put")
            call_oi = sum(o.get("open_interest", 0) or 0 for o in options if o.get("option_type") == "call")
            put_oi = sum(o.get("open_interest", 0) or 0 for o in options if o.get("option_type") == "put")

            spreads = [
                ((o["ask"] - o["bid"]) / o["ask"] * 10000)
                for o in options if o.get("ask") and o.get("bid") and o["ask"] > 0
            ]
            atm_spread = sorted(spreads)[len(spreads) // 2] if spreads else 50.0

            ivs = [g.get("mid_iv") for o in options if (g := o.get("greeks")) and g.get("mid_iv")]
            iv_rank = min(100.0, (sum(ivs) / len(ivs)) * 100) if ivs else 0.0

            return OptionsSnapshot(
                ticker=ticker,
                asof=datetime.now(timezone.utc),
                call_volume=call_v, put_volume=put_v,
                call_oi=call_oi, put_oi=put_oi,
                oi_change_pct=0.0,
                iv_rank=iv_rank, iv_percentile=iv_rank,
                skew=0.0, atm_spread_bps=atm_spread,
                unusual_call_activity=call_v > 3 * max(1, put_v),
            )
        except Exception as exc:  # pragma: no cover
            log.warning("tradier failed for %s (%s); synthetic", ticker, exc)
            return self._synthetic.options_snapshot(ticker)
