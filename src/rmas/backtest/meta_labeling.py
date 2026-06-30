"""Meta-labeling (Lopez de Prado style).

Stage 1 (the gates) produces a *candidate* with a side. Stage 2 — this model —
decides Trade / No-Trade by estimating P(positive net return). Only candidates
above ``min_trade_probability`` are taken. This separates "is there a signal"
from "is it worth trading after costs", which is exactly the EV discipline.

The model is intentionally simple and dependency-free: a logistic regression
trained by gradient descent on standardized features. If scikit-learn is
installed you can swap it in, but the stdlib version keeps tests hermetic.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from rmas.mathx import mean, stdev


@dataclass
class MetaLabeler:
    feature_names: list[str]
    weights: list[float] = field(default_factory=list)
    bias: float = 0.0
    mu: list[float] = field(default_factory=list)
    sigma: list[float] = field(default_factory=list)
    fitted: bool = False

    # --------------------------- training --------------------------------- #
    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int],
            lr: float = 0.1, epochs: int = 400, l2: float = 1e-3) -> "MetaLabeler":
        X = [list(map(float, row)) for row in X]
        y = list(map(float, y))
        n, d = len(X), len(self.feature_names)
        if n == 0:
            self.weights = [0.0] * d
            self.fitted = True
            return self

        # standardize
        self.mu = [mean([row[j] for row in X]) for j in range(d)]
        self.sigma = [stdev([row[j] for row in X]) or 1.0 for j in range(d)]
        Xs = [[(row[j] - self.mu[j]) / self.sigma[j] for j in range(d)] for row in X]

        self.weights = [0.0] * d
        self.bias = 0.0
        for _ in range(epochs):
            gw = [0.0] * d
            gb = 0.0
            for i in range(n):
                z = self.bias + sum(self.weights[j] * Xs[i][j] for j in range(d))
                p = _sigmoid(z)
                err = p - y[i]
                for j in range(d):
                    gw[j] += err * Xs[i][j]
                gb += err
            for j in range(d):
                self.weights[j] -= lr * (gw[j] / n + l2 * self.weights[j])
            self.bias -= lr * (gb / n)
        self.fitted = True
        return self

    # --------------------------- inference -------------------------------- #
    def predict_proba(self, features: dict[str, float]) -> float:
        if not self.fitted or not self.weights:
            return 0.5
        z = self.bias
        for j, name in enumerate(self.feature_names):
            x = float(features.get(name, 0.0))
            xs = (x - self.mu[j]) / (self.sigma[j] or 1.0)
            z += self.weights[j] * xs
        return _sigmoid(z)

    def should_trade(self, features: dict[str, float], threshold: float) -> bool:
        return self.predict_proba(features) >= threshold


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)
