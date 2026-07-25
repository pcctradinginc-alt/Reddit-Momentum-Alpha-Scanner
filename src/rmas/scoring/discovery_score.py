"""Discovery gate: is there *early, organic* attention acceleration?"""

from __future__ import annotations

from rmas.mathx import weighted_score
from rmas.scoring.gates import make_gate
from rmas.types import GateResult


def score_discovery(features: dict[str, float], cfg) -> GateResult:
    """``cfg`` is the ``discovery`` Config node (weights + gate thresholds)."""
    weights = cfg.weights.to_dict() if hasattr(cfg.weights, "to_dict") else dict(cfg.weights)
    gate = cfg.gate

    score = weighted_score(features, weights)

    reasons: list[str] = []
    blockers: list[str] = []

    z7 = features.get("_raw_mention_z_7d", 0.0)
    authors = features.get("_raw_unique_authors", 0.0)
    bot = features.get("_raw_bot_ratio", 0.0)
    ape_z = features.get("_raw_apewisdom_accel_z", 0.0)

    # Early-attention requirement: our own RSS mention z-score OR (gated by
    # config) ApeWisdom's same-channel acceleration z-score. ApeWisdom sees
    # comments and aggregates many subs, so it can confirm early attention on
    # a ticker our radar already found even when our own z-score is thin.
    # NOTE: `min_unique_authors` / `max_bot_ratio` below are independent
    # safety filters and are NOT affected by this OR-logic.
    min_z = gate.get("min_mention_z_7d", 1.5)
    use_ape_gate = gate.get("use_apewisdom_accel", True)
    min_ape_z = gate.get("min_apewisdom_accel_z", 1.5)

    if z7 >= min_z:
        reasons.append(f"mention_z_7d={z7:.2f}>= {min_z}")
    elif use_ape_gate and ape_z >= min_ape_z:
        reasons.append(f"apewisdom_accel_z={ape_z:.2f}>={min_ape_z} "
                       f"(mention_z_7d={z7:.2f}<{min_z})")
    else:
        msg = f"mention_z_7d={z7:.2f}<{min_z}"
        if use_ape_gate:
            msg += f" and apewisdom_accel_z={ape_z:.2f}<{min_ape_z}"
        blockers.append(msg)

    min_authors = gate.get("min_unique_authors", 5)
    if authors < min_authors:
        blockers.append(f"unique_authors={authors:.0f}<{min_authors}")

    max_bot = gate.get("max_bot_ratio", 0.45)
    if bot > max_bot:
        blockers.append(f"bot_ratio={bot:.2f}>{max_bot}")
    elif bot > 0:
        reasons.append(f"bot_ratio={bot:.2f}_ok")

    return make_gate("discovery", score, gate.get("min_score", 0.6), reasons, blockers)
