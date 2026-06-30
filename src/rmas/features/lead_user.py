"""Lead-user quality model with overfit protection.

Some authors are repeatedly early to moves that work; others are pump-and-dump
risks. We track each author's forward-return track record and convert it into a
bounded reputation weight in [0,1]. Overfit protection:

  * Bayesian shrinkage toward the population mean for low sample counts.
  * A pump penalty for authors whose calls spike then collapse.
  * Hard cap on how much any single author can boost a signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rmas.mathx import clip


@dataclass
class AuthorRecord:
    author: str
    n_calls: int = 0
    avg_forward_return: float = 0.0     # mean fwd return after their calls
    early_hit_rate: float = 0.0         # fraction where they were early & right
    pump_rate: float = 0.0              # fraction that spiked then dumped


@dataclass
class LeadUserModel:
    prior_return: float = 0.0           # population mean forward return
    prior_strength: float = 10.0        # pseudo-count for shrinkage
    max_weight: float = 1.0
    records: dict[str, AuthorRecord] = field(default_factory=dict)

    def reputation(self, author: str) -> float:
        rec = self.records.get(author)
        if rec is None or rec.n_calls == 0:
            return 0.0
        # Bayesian shrinkage: few calls -> pulled toward prior (~0 edge).
        n = rec.n_calls
        shrunk = (n * rec.avg_forward_return + self.prior_strength * self.prior_return) / (
            n + self.prior_strength
        )
        edge = clip(shrunk / 0.05)                      # 5% fwd return -> full
        quality = clip(0.6 * edge + 0.4 * rec.early_hit_rate)
        quality *= (1.0 - clip(rec.pump_rate))          # penalize pumpers
        return clip(quality * self.max_weight, 0.0, self.max_weight)

    def aggregate_weight(self, authors: list[str]) -> float:
        """Combined lead-user weight for the set of authors discussing a ticker.

        Uses the best few reputations (not the sum) so one ticker isn't
        dominated by a crowd of low-quality accounts.
        """
        if not authors:
            return 0.0
        reps = sorted((self.reputation(a) for a in set(authors)), reverse=True)
        top = reps[:3]
        return clip(sum(top) / len(top)) if top else 0.0
