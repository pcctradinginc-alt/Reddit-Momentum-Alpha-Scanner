# Reddit-Momentum-Alpha-Scanner (RMAS)

> Treat Reddit as a **radar for early retail attention** — then *confirm* with
> price/volume/options flow, *filter out* FOMO/pump risk, and emit only a few
> high-quality setups with **positive expected value after costs**.
>
> **Optimization target:** EV-after-cost, Profit Factor, Sortino, Max Drawdown,
> and stability across regimes — **not** hit-rate. Few high-quality trades beat
> many Reddit-FOMO signals.

This is a research/decision-support scanner. **It does not place real orders.**
Paper-trading is a local simulation. Nothing here is financial advice.

---

## How it thinks: three gates, all must be GREEN

A ticker only becomes an alert when **all three** gates pass:

| Gate | Question | Key features |
|------|----------|--------------|
| **1. Discovery** | Is attention *accelerating early*? | mention z-score vs 7/30d, acceleration, threads/comments per hour, unique authors & growth, subreddit diversity, lead-user weight, **bot/shill filter** |
| **2. Tradeability** | Does price/volume/options *confirm*? | rel. strength vs SPY/QQQ/sector, 20d breakout, VWAP/EMA, rel. volume, premarket volume, call/put imbalance, OI change, IV rank — plus **liquidity hard-filters** (market cap, $-volume, spread) |
| **3. Timing/Risk** | Is there a *trigger* and is it *not overheated*? | breakout / pullback-to-VWAP / ORB / post-earnings-drift / squeeze; **blow-off filter** (parabolic, gap, IV explosion, mainstream coverage, attention decay) |

On top: a **regime filter** (VIX / trend / breadth) scales size and can hard-block
new longs; **cross-source attention** rewards Reddit-early *divergence* and
penalizes broad euphoria; a **catalyst module** separates a real catalyst from
pure meme hype; and **meta-labeling** (a 2nd model) makes the final
trade/no-trade call. Output is **ranked** — only the top 1–3 setups per day.

```
ingest reddit → extract+disambiguate tickers → bot filter
  → DISCOVERY gate → market/options/liquidity → TRADEABILITY gate
  → timing inputs → TIMING/RISK gate → regime filter → blow-off veto
  → strategy select → meta-label → rank → top N → risk-defined TradePlan → email
```

---

## Quickstart (runs offline, **no keys needed**)

```bash
# 1) clone & enter
git clone https://github.com/pcctradinginc-alt/Reddit-Momentum-Alpha-Scanner.git
cd Reddit-Momentum-Alpha-Scanner

# 2) (optional) virtualenv, then install
python -m pip install -e ".[all]"        # full install (live adapters + dev)
# the CORE engine + tests need nothing but PyYAML/pydantic; ".[all]" adds adapters

# 3) run the test suite
pytest                                    # 62 tests, all offline

# 4) end-to-end smoke test (synthetic data)
rmas selfcheck
rmas scan                                 # ranked setups to stdout
rmas alert --dry-run                      # full report (saved to reports/)
rmas paper                                # open paper positions (local blotter)
python -m scripts.demo_backtest           # walk-forward + meta-labeling demo
```

Everything defaults to **offline mode** (`RMAS_OFFLINE=true`) using deterministic
synthetic adapters, so the whole pipeline runs with zero credentials. Flip to
live data with real keys (below) and `rmas --live scan`.

---

## Configuration: API keys & secrets

**Keys are never stored in code or YAML.** Copy `.env.example` → `.env` and fill
in only what you have; missing integrations fall back to synthetic/cached data.

```bash
cp .env.example .env
# edit .env — these are read at runtime via src/rmas/config.py
```

| Integration | Vars | Used for |
|---|---|---|
| Reddit (PRAW) | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` | attention ingestion |
| Alpaca | `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `ALPACA_DATA_URL` | price/volume bars, liquidity |
| Tradier | `TRADIER_API_KEY`, `TRADIER_BASE_URL` | options chain / flow |
| Polygon / Finnhub | `POLYGON_API_KEY`, `FINNHUB_API_KEY` | bars fallback, news/catalysts |
| Google Trends / X | (pytrends needs no key), `X_BEARER_TOKEN` | cross-source attention |
| Email (SMTP) | `SMTP_HOST/PORT/USER/PASSWORD`, `ALERT_EMAIL_FROM/TO` | alert delivery |

> Gmail: create an **App Password** at <https://myaccount.google.com/apppasswords>
> and put it in `SMTP_PASSWORD`. Recipient defaults to `ALERT_EMAIL_TO`.

---

## Tunable gates & thresholds

All gates/weights/limits live in `config/` and are hot-tunable:

- `config/config.yaml` — gate thresholds, weights, costs, risk limits, regime, meta-label.
- `config/strategies.yaml` — the four strategies (A–D), each separately enabled/tuned.
- `config/universe.yaml` — subreddits + **ticker disambiguation** lists
  (ambiguous words like `AI/ON/NOW/CAT/DD/ARE/FOR` are only counted with `$TICKER`,
  company name, cashtag, option-chain, or context).

---

## Strategies (separately implemented & backtested)

- **A — Early Reddit Momentum Long** (stock/ETF, 1–10d): the core long.
- **B — Reddit Squeeze Watch**: high short interest + small/mid float + call flow + breakout.
- **C — Post-Earnings Reddit Drift**: recent earnings/guidance + price strength + *nascent* Reddit attention.
- **D — Blow-Off Avoid/Fade**: primarily a **long blocker**; optional small defined-risk fade (off by default).

---

## Backtest discipline

- **Point-in-time**, no lookahead (fills at next bar open), no survivorship bias.
- Signal **session preserved** (premarket / intraday / afterhours).
- **Realistic costs**: spread, slippage, commissions, options mid/fill, IV-crush haircut.
- **Walk-forward** (anchored train/test), never in-sample optimization.
- **Regime-segmented** output (meme phase / bear / AI-bull / momentum-rout).
- Targets capture **MFE, MAE, return-after-cost, drawdown** — and **meta-labeling**
  (raw signal → candidate; 2nd model → trade/no-trade).

Report metrics: **EV-after-cost, Profit Factor, Sortino, Sharpe, Max Drawdown,
Win Rate, Avg Win/Loss, Payoff, trades per regime.**

---

## Project layout

```
config/                 # YAML gates, strategies, universe (disambiguation)
src/rmas/
  config.py             # YAML + env/secrets loader (keys only from env)
  logging_setup.py
  types.py              # domain dataclasses
  mathx.py              # stdlib stats (zscore, ema, atr, ...)
  nlp/                  # ticker_extraction (disambiguation) + bot_detection
  features/             # discovery, momentum, options_flow, liquidity,
                        # catalyst, regime, lead_user, cross_source
  scoring/              # discovery / tradeability / timing_risk gates
  strategies/           # A/B/C/D + shared trade-plan builder
  risk/                 # position_sizing + portfolio limits
  backtest/             # costs, metrics, engine, walkforward, meta_labeling
  paper/                # local paper blotter (no real orders)
  alerts/               # report (text/html) + SMTP email
  data/                 # adapters (reddit/alpaca/tradier/polygon/news/trends)
                        # + synthetic offline generators + cache
  pipeline/scan.py      # the daily orchestration
  cli.py                # rmas scan|alert|paper|backtest|selfcheck
tests/                  # 62 offline tests
scripts/demo_backtest.py
```

---

## Email alert format

An alert is emitted **only** when Discovery + Tradeability + Timing/Risk are all
green. Each recommendation states: **why it's a signal, why it's tradeable, why
now, the risk, the exit, and the expected edge** — e.g.:

```
━━━ PLTR  [A_early_momentum_long]  LONG equity ━━━
  Entry 154.40 | Stop 143.72 | Targets [170.42, 186.44]
  Size 52 sh | Risk $555.29 | Time-stop 10d
  Why signal : Early attention acceleration (discovery 0.97: mention_z_7d=22.96)
  Why tradeable: Price/volume/options confirm (rel_strength=0.062; rel_volume=2.57)
  Why now    : Trigger fired & not overheated (pullback_vwap)
  Risk       : Stop 143.72 (2.0x ATR), risk $555.29
  Exit       : Targets [170.42, 186.44]; time stop 10d; trailing stop on close.
  Edge       : ~217 bps expected edge after modeled costs.
```

---

## Roadmap / status

MVP 1–7 implemented: structure+config+logging+tests · Reddit ingestion +
disambiguation · price/volume momentum · the three gates · backtest+costs ·
paper-trading+alerts · options flow / catalyst / regime / meta-labeling.

Next: wire real point-in-time historical data for genuine walk-forward research,
expand catalyst sources (SEC/earnings calendars), and calibrate the meta-label
model on live forward returns.

## License

MIT
