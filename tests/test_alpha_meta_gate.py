"""Meta-labeling gate: training from outcomes, JSON persistence, and the
get_active_model status strings the CLI prints.

All hermetic: synthetic outcome records written to tmp paths, no network.
"""

from __future__ import annotations

import json

from rmas.alpha import meta_gate
from rmas.alpha.meta_gate import (
    META_FEATURES,
    get_active_model,
    load_model,
    save_model,
    train_from_outcomes,
)
from rmas.config import load_config


def _make_outcome(win: bool, strength: float) -> dict:
    """A synthetic outcome record shaped like paper.broker._append_outcome's
    output. `strength` drives a clearly separable feature so a fitted model
    should recover the win/loss split."""
    return {
        "ticker": "TEST",
        "strategy": "A_early_momentum_long",
        "r_multiple": 1.5 if win else -1.0,
        "win": win,
        "features": {
            "_raw_rel_strength": strength,
            "_raw_rel_volume": 2.0 if win else 0.5,
            "_raw_r5": 0.05 if win else -0.05,
            "_raw_gap_pct": 0.01,
            "above_vwap": 1.0 if win else 0.0,
            "_raw_iv_rank": 40.0,
            "_raw_call_put_imbalance": 0.2 if win else -0.2,
            "cross_source_earliness": 0.6 if win else 0.1,
            "catalyst_score": 0.5,
            "x_watchers_growth": 0.1,
            "hype_growth": 0.1,
        },
    }


def _write_outcomes(path, n_wins: int, n_losses: int) -> list[dict]:
    records = []
    for i in range(n_wins):
        records.append(_make_outcome(True, strength=1.0 + 0.05 * i))
    for i in range(n_losses):
        records.append(_make_outcome(False, strength=-1.0 - 0.05 * i))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records))
    return records


# --------------------------------------------------------------------------- #
def test_train_from_outcomes_fits_above_threshold(tmp_path):
    path = tmp_path / "outcomes.json"
    _write_outcomes(path, n_wins=20, n_losses=20)

    model = train_from_outcomes(min_samples=30, path=path)
    assert model is not None
    assert model.fitted
    assert model.feature_names == META_FEATURES

    # a clearly-winning feature snapshot should score higher than a losing one
    win_p = model.predict_proba(_make_outcome(True, strength=1.5)["features"])
    loss_p = model.predict_proba(_make_outcome(False, strength=-1.5)["features"])
    assert win_p > loss_p


def test_train_from_outcomes_below_min_samples_returns_none(tmp_path):
    path = tmp_path / "outcomes.json"
    _write_outcomes(path, n_wins=5, n_losses=5)   # 10 total < 30

    assert train_from_outcomes(min_samples=30, path=path) is None


def test_train_from_outcomes_single_class_returns_none(tmp_path):
    path = tmp_path / "outcomes.json"
    _write_outcomes(path, n_wins=35, n_losses=0)  # enough total, but all wins

    assert train_from_outcomes(min_samples=30, path=path) is None


def test_train_from_outcomes_missing_file_returns_none(tmp_path):
    path = tmp_path / "does_not_exist.json"
    assert train_from_outcomes(min_samples=30, path=path) is None


def test_train_from_outcomes_corrupt_file_returns_none(tmp_path):
    path = tmp_path / "outcomes.json"
    path.write_text("{not valid json")
    assert train_from_outcomes(min_samples=30, path=path) is None


# --------------------------------------------------------------------------- #
def test_save_load_roundtrip_predict_proba_matches(tmp_path):
    outcomes_path = tmp_path / "outcomes.json"
    _write_outcomes(outcomes_path, n_wins=20, n_losses=20)
    model = train_from_outcomes(min_samples=30, path=outcomes_path)
    assert model is not None

    model_path = tmp_path / "meta_model.json"
    save_model(model, path=model_path)
    assert model_path.exists()

    loaded = load_model(path=model_path)
    assert loaded is not None
    assert loaded.fitted
    assert loaded.feature_names == model.feature_names

    sample_features = _make_outcome(True, strength=0.7)["features"]
    p_original = model.predict_proba(sample_features)
    p_loaded = loaded.predict_proba(sample_features)
    assert abs(p_original - p_loaded) < 1e-9


def test_load_model_missing_file_returns_none(tmp_path):
    assert load_model(path=tmp_path / "nope.json") is None


def test_load_model_corrupt_file_returns_none(tmp_path):
    path = tmp_path / "meta_model.json"
    path.write_text("not json at all")
    assert load_model(path=path) is None


# --------------------------------------------------------------------------- #
def test_get_active_model_trains_and_reports_active(tmp_path, monkeypatch):
    outcomes_path = tmp_path / "outcomes.json"
    model_path = tmp_path / "meta_model.json"
    _write_outcomes(outcomes_path, n_wins=20, n_losses=20)
    monkeypatch.setattr(meta_gate, "OUTCOMES", outcomes_path)
    monkeypatch.setattr(meta_gate, "META_MODEL_PATH", model_path)

    cfg = load_config()
    model, status = get_active_model(cfg, refresh=True)
    assert model is not None
    assert "ACTIVE" in status
    assert "trained on 40 outcomes" in status
    assert model_path.exists()   # persisted for future warm-start


def test_get_active_model_loads_persisted_when_no_fresh_training(tmp_path, monkeypatch):
    outcomes_path = tmp_path / "outcomes.json"      # never created -> no fresh data
    model_path = tmp_path / "meta_model.json"
    monkeypatch.setattr(meta_gate, "OUTCOMES", outcomes_path)
    monkeypatch.setattr(meta_gate, "META_MODEL_PATH", model_path)

    # seed a previously-trained model on disk
    seed_outcomes = tmp_path / "seed_outcomes.json"
    _write_outcomes(seed_outcomes, n_wins=20, n_losses=20)
    seeded = train_from_outcomes(min_samples=30, path=seed_outcomes)
    assert seeded is not None
    save_model(seeded, path=model_path)

    cfg = load_config()
    model, status = get_active_model(cfg, refresh=True)
    assert model is not None
    assert "ACTIVE (loaded)" in status


def test_get_active_model_warming_up_when_nothing_available(tmp_path, monkeypatch):
    outcomes_path = tmp_path / "outcomes.json"
    model_path = tmp_path / "meta_model.json"
    monkeypatch.setattr(meta_gate, "OUTCOMES", outcomes_path)
    monkeypatch.setattr(meta_gate, "META_MODEL_PATH", model_path)

    cfg = load_config()
    model, status = get_active_model(cfg, refresh=True)
    assert model is None
    assert "WARMING UP" in status
    assert "0/" in status


def test_get_active_model_norefresh_uses_persisted_only(tmp_path, monkeypatch):
    outcomes_path = tmp_path / "outcomes.json"
    model_path = tmp_path / "meta_model.json"
    # plenty of fresh data available, but refresh=False must not retrain
    _write_outcomes(outcomes_path, n_wins=20, n_losses=20)
    monkeypatch.setattr(meta_gate, "OUTCOMES", outcomes_path)
    monkeypatch.setattr(meta_gate, "META_MODEL_PATH", model_path)

    cfg = load_config()
    model, status = get_active_model(cfg, refresh=False)
    assert model is None
    assert "WARMING UP" in status
