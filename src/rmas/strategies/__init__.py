"""Four separately-testable strategies:

  A) Early Reddit Momentum Long
  B) Reddit Squeeze Watch
  C) Post-Earnings Reddit Drift
  D) Blow-Off Avoid/Fade (primarily a long blocker)
"""

from rmas.strategies.base import STRATEGY_REGISTRY, Strategy, build_trade_plan  # noqa: F401

# Importing these registers each strategy into STRATEGY_REGISTRY via @register.
from rmas.strategies import (  # noqa: E402,F401
    blowoff_fade,
    early_momentum,
    post_earnings_drift,
    squeeze,
)
