"""Alpha Engine: the layer that turns raw gate output into a self-improving
signal-quality loop.

Three pieces live here:
  * ``meta_gate`` — trains/loads the Stage-2 meta-labeling model from the
    paper-blotter feedback loop (``data/state/outcomes.json``) and activates
    the ``use_meta`` branch in ``pipeline.scan.run_scan`` that was previously
    always dark (meta_model was never passed in).
  * ``forward_log`` — logs every alerted setup's entry reference price and
    backfills REAL forward returns at fixed horizons, independent of the
    paper blotter's simulated exit logic. This is the raw measure of "was the
    signal itself any good," not "did our exit rules do well."

Both modules are pure stdlib + PyYAML (via ``rmas.config``), run offline in
tests, and never raise into the calling scan/CLI — a broken alpha layer must
degrade to "no meta gate / no forward log," never crash a scan.
"""

from __future__ import annotations
