"""The daily scan: from raw Reddit chatter to a few ranked, risk-defined plans.

Flow (each step can veto):
    ingest reddit -> extract+disambiguate tickers -> bot filter
      -> DISCOVERY gate (early attention)
      -> market/options/liquidity -> TRADEABILITY gate (confirmation)
      -> timing inputs -> TIMING/RISK gate (trigger + not overheated)
      -> regime filter (size + hard block)
      -> blow-off veto
      -> strategy selection -> meta-label (trade/no-trade)
      -> rank -> top N -> TradePlan

Runs fully offline (synthetic adapters) with zero credentials.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from rmas.backtest.meta_labeling import MetaLabeler
from rmas.config import Config, Secrets, is_offline, load_config
from rmas.data.base import synthetic_attention_series
from rmas.data.market_alpaca import AlpacaAdapter
from rmas.data.news_source import NewsAdapter
from rmas.data.options_tradier import TradierAdapter
from rmas.data.reddit_source import RedditAdapter
from rmas.data.trends_source import TrendsAdapter
from rmas.features.catalyst import detect_catalyst
from rmas.features.cross_source import CrossSourceInput, cross_source_score
from rmas.features.discovery import DiscoveryInput, EarlyAttention, build_discovery_features
from rmas.features.momentum import MomentumInput, build_momentum_features, is_parabolic
from rmas.features.options_flow import build_options_features
from rmas.features.regime import RegimeInput, RegimeState, classify_regime
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


@dataclass
class ScanResult:
    asof: datetime
    candidates: list[Candidate] = field(default_factory=list)
    plans: list[TradePlan] = field(default_factory=list)
    regime: RegimeState | None = None
    rejected: dict[str, int] = field(default_factory=dict)
    reddit_mode: str = "synthetic"      # "oauth" | "public_json" | "synthetic"

    @property
    def actionable(self) -> bool:
        """Alerts are only real when the attention radar (Reddit) is live."""
        return self.reddit_mode in ("oauth", "public_json")

    def summary(self) -> str:
        flag = "" if self.actionable else "  [DEGRADED: reddit synthetic — NOT actionable]"
        return (f"asof={self.asof:%Y-%m-%d} regime={self.regime.regime if self.regime else '?'} "
                f"reddit={self.reddit_mode} candidates={len(self.candidates)} plans={len(self.plans)} "
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
    reddit = RedditAdapter(secrets, offline=off)
    market = AlpacaAdapter(secrets, offline=off)
    options = TradierAdapter(secrets, offline=off)
    news = NewsAdapter(secrets, offline=off)
    trends = TrendsAdapter(secrets, offline=off)

    xcfg = ExtractionConfig.from_universe(cfg.to_dict())

    subs = list(cfg.universe.subreddits)
    lookback_h = 24

    # ---- 1) ingest + extract + bot filter ----
    raw = reddit.fetch_mentions(subs, lookback_h)
    mentions = _assign_tickers(raw, xcfg)
    score_mentions(mentions, BotConfig())

    by_ticker: dict[str, list[Mention]] = defaultdict(list)
    for m in mentions:
        by_ticker[m.ticker].append(m)

    # ---- regime (global) ----
    regime = classify_regime(RegimeInput(
        vix=float(cfg.regime.get("vix_calm_below", 16)) + 4,  # synthetic-neutral default
        index_above_200dma=True, index_20d_return=0.02,
        breadth_pct_above_50dma=60.0, momentum_factor_5d_return=0.0,
    ), vix_risk_off=cfg.regime.get("vix_risk_off_above", 28),
       vix_calm=cfg.regime.get("vix_calm_below", 16))

    candidates: list[Candidate] = []

    for ticker, ms in by_ticker.items():
        # ---- discovery features ----
        attn_hist = synthetic_attention_series(ticker, days=30)
        today_mentions = len(ms)
        mentions_daily = attn_hist[:-1] + [float(max(today_mentions, attn_hist[-1]))]
        uniq_authors = len({m.author for m in ms})
        br = bot_ratio(ms)

        dinp = DiscoveryInput(
            ticker=ticker,
            mentions_daily=mentions_daily,
            new_threads_hourly=[max(1, today_mentions // 8)] * 6 + [today_mentions // 4],
            comments_hourly=[max(1, today_mentions // 4)] * 6 + [today_mentions // 2],
            unique_authors_daily=[max(1, uniq_authors - 2)] * 6 + [uniq_authors],
            subreddit_count=len({m.subreddit for m in ms}),
            bot_ratio=br,
        )
        dfeat = build_discovery_features(dinp,
                                         cfg.universe.get("lookback_short_days", 7),
                                         cfg.universe.get("lookback_long_days", 30))
        dgate = score_discovery(dfeat, cfg.discovery)
        if not dgate.green:
            rejected["discovery"] += 1
            continue

        # ---- tradeability ----
        bars = market.daily_bars(ticker, 60)
        liq = market.liquidity(ticker)
        opt = options.options_snapshot(ticker)
        minp = MomentumInput(
            ticker=ticker, bars=bars,
            benchmark_returns={"SPY": 0.01, "QQQ": 0.012, "SECTOR": 0.008},
        )
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

        # ---- timing/risk ----
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
            mainstream_coverage=False,
            is_top_hype_ticker=False,
        )
        tr_gate = score_timing_risk(tinp, cfg.timing_risk)
        if not tr_gate.green:
            rejected["timing_risk"] += 1
            continue

        # ---- cross-source earliness (informational weight on rank) ----
        cs = cross_source_score(CrossSourceInput(
            reddit_attention_z=dfeat.get("_raw_mention_z_7d", 0.0),
            google_trends_z=trends.google_trends_z(ticker),
            x_attention_z=trends.x_attention_z(ticker),
            news_count_z=float(len(news.headlines(ticker))) - 1.0,
        ), prefer_divergence=cfg.cross_source.get("prefer_divergence", True),
           penalize_broad=cfg.cross_source.get("penalize_broad_euphoria", True))

        cat = detect_catalyst([m.text for m in ms], news.headlines(ticker))

        cand = Candidate(
            ticker=ticker, asof=asof, signal_time=SignalTime.INTRADAY,
            discovery=dgate, tradeability=tgate, timing_risk=tr_gate,
            features={**feats,
                      "cross_source_earliness": cs["cross_source_earliness"],
                      "catalyst_score": cat.score,
                      "_raw_rel_strength": mfeat.get("_raw_rel_strength", 0.0)},
        )
        # blended rank score (rewards earliness + real catalyst).
        cand.rank_score = round(
            0.35 * dgate.score + 0.30 * tgate.score + 0.20 * tr_gate.score
            + 0.10 * cs["cross_source_earliness"] + 0.05 * cat.score, 4
        )
        candidates.append(cand)

    # ---- regime hard block ----
    if regime.block_new_longs:
        log.info("regime %s blocks new longs", regime.regime)
        return ScanResult(asof, candidates, [], regime, dict(rejected), reddit.mode)

    # ---- strategy + blow-off + meta-label + rank ----
    candidates.sort(key=lambda c: c.rank_score, reverse=True)
    plans: list[TradePlan] = []
    blowoff = BlowOffFade()
    min_p = cfg.meta_labeling.get("min_trade_probability", 0.55)
    use_meta = cfg.meta_labeling.get("enabled", True) and meta_model is not None

    for cand in candidates:
        atr = cand.features.get("_raw_atr", 0.0) or cand.features.get("_raw_close", 1.0) * 0.03
        ctx = StrategyContext(
            close=cand.features.get("_raw_close", 0.0),
            atr=atr,
            short_interest_pct=0.0,
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

        # pick the first registered long strategy that qualifies
        chosen = None
        for name, strat in STRATEGY_REGISTRY.items():
            if getattr(strat, "direction", "long") != "long":
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

        plan = build_trade_plan(cand, ctx, strat,
                                time_stop_days=cfg.risk.get("time_stop_days_max", 10))
        plans.append(plan)

    max_alerts = cfg.run.get("max_alerts_per_day", 3)
    plans = plans[:max_alerts]
    return ScanResult(asof, candidates, plans, regime, dict(rejected), reddit.mode)
