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

## Fully automated operation & running costs

The repo runs itself via GitHub Actions (`.github/workflows/scan.yml`):
every US trading weekday pre-market it scans, and emails you only when there
are actionable setups. **Total running cost: $0** — by design, and enforced:

| Cost lever | How it's kept at zero |
|---|---|
| CI minutes | Stdlib **holiday/weekend gate** exits before `pip install` (closed days ≈ 10s); slim `.[ci]` install; pip cache; 12-min timeout; ~60 min/month vs 2000 free |
| Market data | Alpaca free tier; bars cached per day; benchmarks (SPY/QQQ) fetched **once per scan**; liquidity reuses already-fetched bars |
| API call volume | Market/options/news APIs only hit for discovery-green tickers, hard-capped at `run.max_symbols_market_data` (default 25) per scan |
| Reddit | Free: OAuth app or the built-in **no-auth public-JSON mode** |
| Options / news / fundamentals | Tradier + Finnhub free tiers, responses cached per day |
| Email | Gmail SMTP (App-Password), free; **no email on empty days** (`run.email_when_no_setups: false`) — reports still saved as artifacts |

Signal integrity in live mode:

- **Real attention history**: daily mention counts persist in
  `data/state/attention_history.json` (kept warm across CI runs via
  `actions/cache`), so discovery z-scores compare today against *genuine*
  history. Cold start is honest: a ticker needs ~5 recorded days before it can
  fire — expect the first alerts after about a week of live runs.
- **Real regime filter**: VIX proxy from 20d realized SPY volatility, 200dma
  trend, breadth and QQQ-SPY momentum spread — computed from bars already paid
  for (i.e. free).
- **Real relative strength**: SPY/QQQ benchmark returns from live bars.
- **Real spread + market cap** in the liquidity hard-filter (Alpaca quote +
  Finnhub profile), with synthetic fallback only when a key is missing.
- **Degraded-run guard**: if Reddit fell back to synthetic, the run never
  emails (unless `--force`).

To activate: add the repository Secrets listed at the top of
`.github/workflows/scan.yml` (at minimum `ALPACA_API_KEY`, `ALPACA_API_SECRET`,
`SMTP_PASSWORD`) — everything else has working defaults. If your git token
lacks the `workflow` scope, run `./scripts/enable_ci.sh` once (refreshes the
scope and pushes the workflow).

### Local automation (no cloud at all)

`./scripts/local_autorun_install.sh` installs a macOS LaunchAgent that runs
the scan every weekday at 14:35 local (~08:35 ET pre-market) directly on your
machine — same holiday gate, same guards, logs to `logs/local_scan.log`.
Fill `.env` with real keys to arm it; without keys it runs dry and emails
nothing. Uninstall: `./scripts/local_autorun_install.sh uninstall`. Run only
ONE of the two automations with live keys, or you'll get duplicate emails.

The degraded-run guard requires **both** live Reddit AND live market data
before an email leaves the machine — a half-synthetic run can never send
real-looking signals.

### X (Twitter) cross-check — without any X API

Every keyless path into X is dead (Nitter 403s, xcancel whitelisting,
login-walled search), so the X-style attention signal comes from
**StockTwits** (keyless public JSON, the finance-X crowd): message volume,
unique posters, sentiment and **watchlist inflow** per ticker.
StockTwits blocks datacenter IPs, so the **local rail feeds CI**: the
`com.rmas.xfeed` LaunchAgent runs `rmas xfeed` weekdays 14:05 (home IP),
publishes `data/state/x_*.json` to the `x-state` branch, and the CI scan
restores them 25 minutes later. Scoring is **alpha-safe by construction**:
`x_attention_z` needs ≥5 days of the ticker's own history (else neutral 0),
is clamped ±3 and only modifies rank via the existing divergence term; the
new watchlist-growth bonus (`cross_source.x_watchers_bonus`) is additive-only
and 0 when unknown — a dark X channel behaves exactly like before the feature.

---

## Roadmap / status

MVP 1–7 implemented: structure+config+logging+tests · Reddit ingestion +
disambiguation · price/volume momentum · the three gates · backtest+costs ·
paper-trading+alerts · options flow / catalyst / regime / meta-labeling.
Plus: full automation (scheduled CI + holiday gate + persisted attention
history) with live benchmark/regime/liquidity wiring at $0 running cost.

Next: wire real point-in-time historical data for genuine walk-forward research,
expand catalyst sources (SEC/earnings calendars), and calibrate the meta-label
model on live forward returns (the meta-label gate stays inactive until a
trained model is supplied).

## License

MIT
