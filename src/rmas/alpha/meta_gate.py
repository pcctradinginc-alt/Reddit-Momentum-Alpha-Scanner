"""Activates the meta-labeling gate from real paper-trade feedback.

``pipeline.scan.run_scan`` has always accepted a ``meta_model`` argument, but
nothing ever built one — every scan called it with ``meta_model=None``, so
``use_meta`` in the strategy loop was permanently False. Meanwhile
``paper.broker._append_outcome`` has been quietly accumulating exactly the
training data the gate needs in ``data/state/outcomes.json`` (features +
win/loss + R-multiple) every time a paper position closes.

This module closes the loop:
  1. ``train_from_outcomes`` builds a fresh ``MetaLabeler`` from the outcome
     log whenever there is enough *balanced* history (>= min_samples total,
     >= 3 wins AND >= 3 losses) — an honest cold start otherwise.
  2. ``save_model``/``load_model`` persist the fitted model as JSON under
     ``data/state/`` so a previously-trained model survives even a run with
     too little fresh data to retrain (e.g. a CI run right after a cache
     miss).
  3. ``get_active_model`` is the single entrypoint the CLI calls: retrain if
     possible, else fall back to the last persisted model, else go without
     (never raise, never block a scan).
"""

from __future__ import annotations

import json
from pathlib import Path

from rmas.backtest.meta_labeling import MetaLabeler
from rmas.config import ROOT, Config
from rmas.logging_setup import get_logger
from rmas.paper.broker import OUTCOMES

log = get_logger("alpha.meta_gate")

META_MODEL_PATH = ROOT / "data" / "state" / "meta_model.json"

# Fixed, ordered feature set — every key here exists in cand.features by the
# time a Candidate reaches the strategy/meta-label loop in pipeline.scan.
# MetaLabeler.predict_proba() already defaults missing keys to 0.0, so this
# list can be extended later without breaking a previously-trained model's
# JSON (old models simply lack the new weight until retrained).
META_FEATURES: list[str] = [
    "_raw_rel_strength",
    "_raw_rel_volume",
    "_raw_r5",
    "_raw_gap_pct",
    "above_vwap",
    "_raw_iv_rank",
    "_raw_call_put_imbalance",
    "cross_source_earliness",
    "catalyst_score",
    "x_watchers_growth",
    "hype_growth",
]

MIN_WINS = 3
MIN_LOSSES = 3


def train_from_outcomes(min_samples: int = 30, path: Path = OUTCOMES) -> MetaLabeler | None:
    """Fit a MetaLabeler from the accumulated paper-trade outcome log.

    Requires ``min_samples`` total records AND both classes represented with
    at least ``MIN_WINS``/``MIN_LOSSES`` examples — a model trained on an
    all-win (or all-loss) sample would output a degenerate constant
    probability, which is worse than no gate at all. Returns None (honest
    cold start) until that bar is cleared.
    """
    try:
        if not path.exists():
            return None
        records = json.loads(path.read_text())
    except Exception as exc:
        log.warning("outcomes log unreadable (%s); no meta-model training", exc)
        return None

    if not isinstance(records, list) or len(records) < min_samples:
        return None

    wins = [r for r in records if r.get("win")]
    losses = [r for r in records if not r.get("win")]
    if len(wins) < MIN_WINS or len(losses) < MIN_LOSSES:
        return None

    X = [[float((r.get("features") or {}).get(name, 0.0)) for name in META_FEATURES]
         for r in records]
    y = [1 if r.get("win") else 0 for r in records]

    try:
        model = MetaLabeler(list(META_FEATURES)).fit(X, y)
    except Exception as exc:
        log.warning("meta-model fit failed (%s)", exc)
        return None
    log.info("meta-model trained on %d outcomes (%d win / %d loss)",
             len(records), len(wins), len(losses))
    return model


def save_model(model: MetaLabeler, path: Path = META_MODEL_PATH) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "feature_names": model.feature_names,
            "weights": model.weights,
            "bias": model.bias,
            "mu": model.mu,
            "sigma": model.sigma,
            "fitted": model.fitted,
        }
        path.write_text(json.dumps(payload, indent=1))
    except Exception as exc:
        log.warning("meta-model save failed (%s)", exc)


def load_model(path: Path = META_MODEL_PATH) -> MetaLabeler | None:
    try:
        if not path.exists():
            return None
        raw = json.loads(path.read_text())
        return MetaLabeler(
            feature_names=list(raw["feature_names"]),
            weights=list(raw.get("weights", [])),
            bias=float(raw.get("bias", 0.0)),
            mu=list(raw.get("mu", [])),
            sigma=list(raw.get("sigma", [])),
            fitted=bool(raw.get("fitted", False)),
        )
    except Exception as exc:
        log.warning("meta-model load failed (%s); starting without one", exc)
        return None


def get_active_model(cfg: Config, refresh: bool = True) -> tuple[MetaLabeler | None, str]:
    """Single entrypoint the CLI uses to obtain the active meta-model.

    Order of preference: freshly retrained (if enough balanced history) ->
    last persisted model -> none (gate stays open / inactive, never blocks a
    scan). Never raises.
    """
    try:
        n = 0
        try:
            if OUTCOMES.exists():
                data = json.loads(OUTCOMES.read_text())
                n = len(data) if isinstance(data, list) else 0
        except Exception:
            n = 0

        min_samples = int(cfg.meta_labeling.get("min_training_samples", 30))

        if refresh:
            # pass the paths explicitly (rather than relying on the function
            # defaults, which bind at import time) so tests can monkeypatch
            # module-level OUTCOMES/META_MODEL_PATH and have it take effect.
            model = train_from_outcomes(min_samples=min_samples, path=OUTCOMES)
            if model is not None:
                save_model(model, path=META_MODEL_PATH)
                return model, f"meta-label ACTIVE (trained on {n} outcomes)"

        model = load_model(path=META_MODEL_PATH)
        if model is not None:
            return model, "meta-label ACTIVE (loaded)"

        return None, (f"meta-label WARMING UP ({n}/{min_samples} outcomes, "
                      f"both classes needed)")
    except Exception as exc:  # never let the meta-gate break a scan
        log.warning("meta-gate unavailable (%s)", exc)
        return None, f"meta-label unavailable: {exc}"
