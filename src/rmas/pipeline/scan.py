"""The daily scan: from raw Reddit chatter to a few ranked, risk-defined plans.

Flow (each step can veto):
    ingest reddit -> extract+disambiguate tickers -> bot filter
      -> DISCOVERY gate (early attention, real persisted history when live)
      -> cap symbols (bounds API calls)
      -> market/options/liquidity -> TRADEABILITY gate (confirmation)
      -> timing inputs -> TIMING/RISK gate (trigger + not overheated)
      -> regime filter (size + hard block; real SPY/QQQ inputs when live)
      -> blow-off veto
      -> strategy selection -> meta-label (trade/no-trade)
      -> rank -> top N -> TradePlan

Runs fully offline (synthetic adapters) with zero credentials.
Cost discipline: market/options/news APIs are only hit for tickers that
already passed the (free) discovery gate, capped at
``run.max_symbols_market_data`` per scan.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from rmas.backtest.meta_labeling import MetaLabeler
from rmas.config import Config, Secrets, is_offline, load_config
from rmas.data.apewisdom_source import ApeWisdomAdapter
from rmas.data.attention_store import AttentionStore
from rmas.data.base import synthetic_attention_series
from rmas.data.fundamentals_finnhub import FinnhubFundamentals
from rmas.data.market_alpaca import AlpacaAdapter
from rmas.data.news_source import NewsAdapter
from rmas.data.options_tradier import TradierAdapter
from rmas.data.finra_short import FinraShortInterest
from rmas.data.reddit_source import RedditAdapter
from rmas.data.sec_edgar import SECEdgarAdapter, filings_as_headlines
from rmas.data.trends_source import TrendsAdapter
from rmas.data.x_source import XAttentionAdapter
from rmas.features.catalyst import detect_catalyst
from rmas.features.cross_source import CrossSourceInput, cross_source_score
from rmas.features.discovery import DiscoveryInput, EarlyAttention, build_discovery_features
from rmas.features.momentum import MomentumInput, build_momentum_features
from rmas.features.options_flow import build_options_features
from rmas.features.regime import (
    Regime,
    RegimeInput,
    RegimeState,
    classify_regime,
    regime_input_from_bars,
)
from rmas.features.sector_map import sector_etf
from rmas.logging_setup import get_logger
from rmas.nlp.bot_detection import BotConfig, bot_ratio, score_mentions
from rmas.nlp.ticker_extraction import ExtractionConfig, extract_tickers
from rmas.scoring.discovery_score import score_discovery
from rmas.scoring.timing_risk_score import TimingInput, score_timing_risk
from rmas.scoring.tradeability_score import score_tradeability
from rmas.strategies import STRATEGY_REGISTRY, build_trade_plan
from rmas.strategies.base import StrategyContext
from rmas.strategies.blowoff_fade import BlowOffFade
from rmas.types import Candidate, Mention, SignalTime, TradePlan, utcnow

log = get_logger("pipeline.scan")

MAINSTREAM_HEADLINE_COUNT = 10   # this many recent headlines = story is late


@dataclass
class ScanResult:
    asof: datetime
    candidates: list[Candidate] = field(default_factory=list)
    plans: list[TradePlan] = field(default_factory=list)
    regime: RegimeState | None = None
    rejected: dict[str, int] = field(default_factory=dict)
    reddit_mode: str = "synthetic"      # "oauth" | "public_json" | "rss" | "synthetic"
    market_mode: str = "synthetic"      # "live" | "synthetic"

    @property
    def actionable(self) -> bool:
        """Real alerts need BOTH a live attention radar (Reddit) AND live
        market data — a Reddit-live/market-synthetic mix would email
        real-looking but fake-confirmed signals."""
        from rmas.data.reddit_source import LIVE_MODES

        return self.reddit_mode in LIVE_MODES and self.market_mode == "live"

    def summary(self) -> str:
        flag = "" if self.actionable else "  [DEGRADED: synthetic inputs — NOT actionable]"
        return (f"asof={self.asof:%Y-%m-%d} regime={self.regime.regime if self.regime else '?'} "
                f"reddit={self.reddit_mode} market={self.market_mode} "
                f"candidates={len(self.candidates)} plans={len(self.plans)} "
                f"rejected={dict(self.rejected)}{flag}")


def _assign_tickers(mentions: list[Mention], xcfg: ExtractionConfig) -> list[Mention]:
    """Ensure every mention has a ticker; explode multi-ticker texts."""
    out: list[Mention] = []
    for m in mentions:
        if m.ticker:
            out.append(m)
            continue
        for sym in extract_tickers(m.text, xcfg):
            out.append(Mention(
                ticker=sym, author=m.author, subreddit=m.subreddit,
                created_utc=m.created_utc, text=m.text, score=m.score,
                is_submission=m.is_submission, permalink=m.permalink,
                author_age_days=m.author_age_days,
            ))
    return out


def _submission_counts(by_ticker: dict[str, list[Mention]]) -> dict[str, int]:
    """Per-ticker SUBMISSION-ONLY mention counts for the persisted attention
    history (`AttentionStore`).

    The RSS comments enrichment pass (reddit_source.py) adds `Mention`
    objects with `is_submission=False` into `by_ticker`. The store's
    historical baseline was built entirely from submission counts, so
    counting comments here too would inflate today's number relative to that
    baseline and fake acceleration in the z-score. Comments still show up
    honestly via `comments_hourly` (no prior baseline to corrupt) and via the
    unique-author count (diversity only, its own history starts now).
    """
    return {t: sum(1 for m in ms if m.is_submission) for t, ms in by_ticker.items()}


def _hourly_counts(ms: list[Mention], asof: datetime, hours: int = 7,
                   submissions: bool = True) -> list[float]:
    """REAL per-hour counts from mention timestamps (oldest -> newest).

    Replaces the previous fabricated hourly series. `submissions` selects
    posts vs comments; RSS mode has no comments -> that series is all-zero
    (z=0, honestly uninformative) until OAuth ingestion adds them.
    """
    buckets = [0.0] * hours
    for m in ms:
        if m.is_submission != submissions:
            continue
        age_h = (asof - m.created_utc).total_seconds() / 3600.0
        if 0 <= age_h < hours:
            buckets[hours - 1 - int(age_h)] += 1
    return buckets


def _time_stop_days(regime: RegimeState, cfg: Config,
                    days_to_earnings: int | None) -> tuple[int, str]:
    """Regime-aware time stop, capped before the next earnings date.

    Risk-on lets momentum breathe (max), neutral runs shorter, risk-off gets
    the minimum. Holding a swing INTO earnings is unpriced event risk, so the
    stop always ends >=1 day before a known earnings date."""
    lo = int(cfg.risk.get("time_stop_days_min", 2))
    hi = int(cfg.risk.get("time_stop_days_max", 10))
    if regime.regime == Regime.RISK_ON:
        days = hi
    elif regime.regime == Regime.NEUTRAL:
        days = max(lo, round((lo + hi) / 2))
    else:
        days = lo
    note = ""
    if days_to_earnings is not None and 0 < days_to_earnings <= days:
        days = max(1, days_to_earnings - 1)
        note = f"Exit BEFORE earnings in {days_to_earnings}d."
    return days, note


def _entry_triggers(mom: dict[str, float]) -> list[str]:
    triggers: list[str] = []
    if mom.get("_raw_breakout_pct", 0.0) > 0:
        triggers.append("breakout_20d")
    if 0 <= mom.get("above_vwap", 0.0) and mom.get("_raw_rel_volume", 0) >= 1.5:
        # near/above VWAP with participation -> treat as reclaim/pullback trigger
        triggers.append("pullback_vwap")
    return triggers


def run_scan(
    cfg: Config | None = None,
    secrets: Secrets | None = None,
    offline: bool | None = None,
    meta_model: MetaLabeler | None = None,
    asof: datetime | None = None,
) -> ScanResult:
    cfg = cfg or load_config()
    secrets = secrets or Secrets()
    off = is_offline(secrets, cfg) if offline is None else offline
    asof = asof or utcnow()

    rejected: Counter = Counter()

    # ---- adapters ----
    reddit = RedditAdapter(secrets, offline=off,
                           min_coverage=cfg.run.get("min_reddit_coverage", 0.5),
                           fetch_comments=cfg.universe.get("fetch_comments_rss", False),
                           comments_per_sub=cfg.universe.get("comments_per_subreddit", 60),
                           rss_budget_seconds=cfg.universe.get("rss_budget_seconds", 620))
    market = AlpacaAdapter(secrets, offline=off)
    options = TradierAdapter(secrets, offline=off)
    news = NewsAdapter(secrets, offline=off)
    trends = TrendsAdapter(secrets, offline=off)
    funda = FinnhubFundamentals(secrets, offline=off)
    hype = ApeWisdomAdapter(secrets, offline=off)
    xattn = XAttentionAdapter(secrets, offline=off)
    sec = SECEdgarAdapter(secrets, offline=off)
    finra = FinraShortInterest(secrets, offline=off)

    xcfg = ExtractionConfig.from_universe(cfg.to_dict())

    subs = list(cfg.universe.subreddits)
    lookback_h = 24

    # ---- 1) ingest + extract + bot filter (free: no market API involved) ----
    raw = reddit.fetch_mentions(subs, lookback_h,
                                limit_per_sub=cfg.universe.get("posts_per_subreddit", 200))
    mentions = _assign_tickers(raw, xcfg)
    score_mentions(mentions, BotConfig())

    by_ticker: dict[str, list[Mention]] = defaultdict(list)
    for m in mentions:
        by_ticker[m.ticker].append(m)

    # ---- attention history: real persisted counts when Reddit is live ----
    store: AttentionStore | None = None
    if reddit.is_live:
        store = AttentionStore()
        # Mention count is SUBMISSIONS ONLY — see `_submission_counts` for why
        # (comment enrichment must not corrupt the historical baseline).
        # Author count includes commenters: that's fine, it only adds
        # diversity and its own history baseline starts now.
        store.record(asof.date(),
                     _submission_counts(by_ticker),
                     {t: len({m.author for m in ms}) for t, ms in by_ticker.items()})
        store.save()

    # ---- regime (global): real SPY/QQQ inputs when market data is live ----
    if not off and secrets.alpaca_ready:
        rinp = regime_input_from_bars(market.daily_bars("SPY", 250),
                                      market.daily_bars("QQQ", 30))
    else:
        rinp = RegimeInput(
            vix=float(cfg.regime.get("vix_calm_below", 16)) + 4,  # synthetic-neutral default
            index_above_200dma=True, index_20d_return=0.02,
            breadth_pct_above_50dma=60.0, momentum_factor_5d_return=0.0,
        )
    regime = classify_regime(rinp,
                             vix_risk_off=cfg.regime.get("vix_risk_off_above", 28),
                             vix_calm=cfg.regime.get("vix_calm_below", 16))

    # ---- 2) DISCOVERY gate for every ticker (still free), then cap ----
    @dataclass
    class _Disc:
        ticker: str
        ms: list[Mention]
        dfeat: dict[str, float]
        dgate: object
        mentions_daily: list[float]

    discovered: list[_Disc] = []
    for ticker, ms in by_ticker.items():
        today_mentions = len(ms)
        if store is not None:
            mentions_daily = store.series(ticker, asof.date(), days=30)
        else:
            attn_hist = synthetic_attention_series(ticker, days=30)
            mentions_daily = attn_hist[:-1] + [float(max(today_mentions, attn_hist[-1]))]
        uniq_authors = len({m.author for m in ms})
        br = bot_ratio(ms)

        if store is not None:
            authors_daily = store.authors_series(ticker, asof.date(), days=7)
        else:  # offline demo approximation
            authors_daily = [float(max(1, uniq_authors - 2))] * 6 + [float(uniq_authors)]

        # ApeWisdom same-channel enrichment: richer than our own RSS radar
        # (includes comments, aggregates many subs) — DISCOVERY input only,
        # never an independent cross-source channel (see apewisdom_source.py).
        ape_accel_z = hype.attention_accel_z(ticker, asof.date())

        dinp = DiscoveryInput(
            ticker=ticker,
            mentions_daily=mentions_daily,
            # real per-hour counts from timestamps (no fabricated shapes)
            new_threads_hourly=_hourly_counts(ms, asof, submissions=True),
            comments_hourly=_hourly_counts(ms, asof, submissions=False),
            unique_authors_daily=authors_daily,
            subreddit_count=len({m.subreddit for m in ms}),
            bot_ratio=br,
            apewisdom_accel=ape_accel_z,
        )
        dfeat = build_discovery_features(dinp,
                                         cfg.universe.get("lookback_short_days", 7),
                                         cfg.universe.get("lookback_long_days", 30))
        dgate = score_discovery(dfeat, cfg.discovery)
        if not dgate.green:
            rejected["discovery"] += 1
            continue
        discovered.append(_Disc(ticker, ms, dfeat, dgate, mentions_daily))

    # Cost cap: only the strongest discovery candidates get (rate-limited)
    # market/options/news calls.
    max_symbols = int(cfg.run.get("max_symbols_market_data", 25))
    discovered.sort(key=lambda d: d.dgate.score, reverse=True)
    if len(discovered) > max_symbols:
        rejected["api_call_cap"] += len(discovered) - max_symbols
        discovered = discovered[:max_symbols]

    # Benchmarks fetched ONCE per scan (cached), not per ticker.
    bench = market.benchmark_returns(20)

    # Build X-attention history for every capped candidate (not just the
    # final greens): the z-scores need day-over-day observations for the
    # recurring names, and 25 keyless StockTwits calls/day are free.
    for d in discovered:
        xattn.metrics(d.ticker, asof.date())

    candidates: list[Candidate] = []
    for d in discovered:
        ticker, ms, dfeat, dgate, mentions_daily = d.ticker, d.ms, d.dfeat, d.dgate, d.mentions_daily

        # ---- tradeability ----
        bars = market.daily_bars(ticker, 60)
        last_close = bars[-1].close if bars else 0.0
        liq = market.liquidity(
            ticker, bars=bars,
            market_cap_usd=funda.market_cap_usd(ticker),
            # consolidated 10d avg $-volume beats IEX-sliver bar volume
            dollar_volume_usd=funda.avg_dollar_volume_usd(ticker, last_close),
            float_shares=funda.shares_outstanding(ticker),
        )
        opt = options.options_snapshot(ticker)

        # real sector benchmark: industry -> SPDR ETF (falls back to SPY)
        bench_t = dict(bench)
        etf = sector_etf(funda.industry(ticker))
        if etf:
            bench_t["SECTOR"] = market.symbol_return(etf)
            # momentum longs against a falling sector underperform — block
            # while the sector ETF is below its 20d MA (None = neutral)
            if (cfg.tradeability.gate.get("require_sector_uptrend", True)
                    and market.above_ma(etf, 20) is False):
                rejected["sector_trend"] += 1
                continue

        pm = market.premarket_volumes(ticker)
        minp = MomentumInput(ticker=ticker, bars=bars, benchmark_returns=bench_t,
                             premarket_volume=pm[0] if pm else 0.0,
                             avg_premarket_volume=pm[1] if pm else 0.0)
        mfeat = build_momentum_features(minp)
        ofeat = build_options_features(opt)
        feats = {**mfeat, **ofeat}
        tgate = score_tradeability(
            feats, cfg.tradeability, liq,
            min_market_cap=cfg.universe.get("min_market_cap_usd", 150_000_000),
            min_dollar_volume=cfg.universe.get("min_dollar_volume_usd", 5_000_000),
        )
        if not tgate.green:
            rejected["tradeability"] += 1
            continue

        # ---- timing/risk (headlines fetched once, reused for cross-source) ----
        headlines = news.headlines(ticker)
        r5_pct = mfeat.get("_raw_r5", 0.0) * 100.0
        gap_pct = abs(mfeat.get("_raw_gap_pct", 0.0)) * 100.0
        early = EarlyAttention(mentions_daily[-5:])
        tinp = TimingInput(
            entry_triggers_fired=_entry_triggers(mfeat),
            intraday_gap_pct=gap_pct,
            parabolic_5d_pct=r5_pct,
            iv_rank=ofeat.get("_raw_iv_rank", 0.0),
            reddit_decaying=early.is_decaying(),
            price_up_while_reddit_down=(mfeat.get("_raw_r5", 0) > 0 and early.is_decaying()),
            mainstream_coverage=len(headlines) >= MAINSTREAM_HEADLINE_COUNT,
            # sitting at the very top of the aggregated hype list = late
            is_top_hype_ticker=(
                (hype.rank(ticker) or 999)
                <= int(cfg.timing_risk.get("top_hype_rank", 3))
            ),
        )
        tr_gate = score_timing_risk(tinp, cfg.timing_risk)
        if not tr_gate.green:
            rejected["timing_risk"] += 1
            continue

        # ---- cross-source earliness (informational weight on rank) ----
        cs = cross_source_score(CrossSourceInput(
            reddit_attention_z=dfeat.get("_raw_mention_z_7d", 0.0),
            google_trends_z=trends.google_trends_z(ticker),
            x_attention_z=xattn.attention_z(ticker, asof.date()),
            news_count_z=float(len(headlines)) - 1.0,
        ), prefer_divergence=cfg.cross_source.get("prefer_divergence", True),
           penalize_broad=cfg.cross_source.get("penalize_broad_euphoria", True))

        # real SEC filings beat reddit chatter as catalyst evidence; kept out
        # of `headlines` so the mainstream/news-count checks stay pure news
        filings = sec.recent_filings(ticker, days=7, asof=asof.date())
        cat = detect_catalyst([m.text for m in ms],
                              headlines + filings_as_headlines(filings))

        # X watchlist inflow: additive-only confirmation bonus (0 when
        # unknown/offline) — by contract it can never REDUCE a rank, so no
        # alpha is lost when the X channel is dark.
        x_watchers = xattn.watchers_growth(ticker, asof.date())
        # ApeWisdom mention growth includes COMMENTS (which our RSS radar
        # can't see) — additive-only confirmation, 0 when unknown.
        hype_growth = hype.growth(ticker)

        cand = Candidate(
            ticker=ticker, asof=asof, signal_time=SignalTime.INTRADAY,
            discovery=dgate, tradeability=tgate, timing_risk=tr_gate,
            features={**feats,
                      "cross_source_earliness": cs["cross_source_earliness"],
                      "catalyst_score": cat.score,
                      "x_watchers_growth": x_watchers,
                      "hype_growth": hype_growth,
                      "_raw_rel_strength": mfeat.get("_raw_rel_strength", 0.0)},
        )
        # blended rank score (rewards earliness + real catalyst) plus the
        # bounded, additive-only confirmation bonuses on top.
        cand.rank_score = round(
            0.35 * dgate.score + 0.30 * tgate.score + 0.20 * tr_gate.score
            + 0.10 * cs["cross_source_earliness"] + 0.05 * cat.score
            + float(cfg.cross_source.get("x_watchers_bonus", 0.05)) * x_watchers
            + float(cfg.cross_source.get("hype_growth_bonus", 0.03)) * hype_growth, 4
        )
        candidates.append(cand)

    # ---- regime hard block ----
    if regime.block_new_longs:
        log.info("regime %s blocks new longs", regime.regime)
        return ScanResult(asof, candidates, [], regime, dict(rejected),
                          reddit.mode, market.mode)

    # ---- strategy + blow-off + meta-label + rank ----
    candidates.sort(key=lambda c: c.rank_score, reverse=True)
    plans: list[TradePlan] = []
    strat_cfg = cfg.to_dict().get("strategies", {}) or {}
    blowoff = BlowOffFade()
    min_p = cfg.meta_labeling.get("min_trade_probability", 0.55)
    use_meta = cfg.meta_labeling.get("enabled", True) and meta_model is not None

    for cand in candidates:
        atr = cand.features.get("_raw_atr", 0.0) or cand.features.get("_raw_close", 1.0) * 0.03
        # real fundamentals for the strategy filters (cached; None -> honest 0)
        shares_out = funda.shares_outstanding(cand.ticker)
        si_pct = finra.short_interest_pct(cand.ticker, shares_out)
        ctx = StrategyContext(
            close=cand.features.get("_raw_close", 0.0),
            atr=atr,
            short_interest_pct=si_pct or 0.0,
            float_shares=shares_out or 0.0,
            days_since_earnings=sec.days_since_earnings(cand.ticker, asof.date()),
            call_imbalance=cand.features.get("_raw_call_put_imbalance", 0.0),
            parabolic_5d_pct=cand.features.get("_raw_r5", 0.0) * 100.0,
            regime_multiplier=regime.size_multiplier if cfg.regime.get("scale_size_by_regime", True) else 1.0,
            account_equity=cfg.risk.get("account_equity_usd", 100_000),
            risk_per_trade_pct=cfg.risk.get("risk_per_trade_pct", 0.75),
            atr_stop_mult=cfg.risk.get("atr_stop_mult", 2.0),
        )

        blocks, reasons = blowoff.blocks_long(cand, ctx)
        if blocks:
            cand.notes.append("blocked_blowoff:" + ",".join(reasons))
            rejected["blowoff"] += 1
            continue

        # pick the first ENABLED long strategy that qualifies (the config
        # enabled-flags are authoritative; the registry only lists code)
        chosen = None
        for name, strat in STRATEGY_REGISTRY.items():
            if getattr(strat, "direction", "long") != "long":
                continue
            if not strat_cfg.get(name, {}).get("enabled", True):
                continue
            ok, why = strat.qualifies(cand, ctx)
            if ok:
                chosen = (strat, why)
                break
        if chosen is None:
            rejected["no_strategy"] += 1
            continue

        strat, why = chosen
        cand.strategy = strat.name

        if use_meta:
            p = meta_model.predict_proba(cand.features)
            cand.meta_probability = round(p, 4)
            if p < min_p:
                rejected["meta_label"] += 1
                continue

        ts_days, exit_note = _time_stop_days(
            regime, cfg, funda.next_earnings_in_days(cand.ticker))
        plan = build_trade_plan(cand, ctx, strat,
                                time_stop_days=ts_days, exit_note=exit_note)
        plans.append(plan)

    max_alerts = cfg.run.get("max_alerts_per_day", 3)
    plans = plans[:max_alerts]
    return ScanResult(asof, candidates, plans, regime, dict(rejected),
                      reddit.mode, market.mode)
