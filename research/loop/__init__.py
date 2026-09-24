"""Phase 5 — the autonomous hypothesis generation loop.

docs/research-loop/README.md's four-stage cycle: Kimi K3 triages the
ranked anomaly table, Astra drafts a falsifiable hypothesis (mechanism +
strategy IR), Kimi K3 adversarially reviews it without seeing any
backtest, and Kimi K3 enumerates IR variants around survivors. Every
surviving-review-or-not candidate is written to research/ledger/.

Deliberately thin (docs/research-loop/PHASE-PROMPTS.md phase 5: "this is
comparatively little code and a lot of prompt iteration"): each stage
module (triage.py, hypothesis.py, review.py, variants.py) is a pure
function of an injected ChatFn (providers.py) plus domain data, cycle.py
only sequences them and writes to the ledger, and config.py is the one
place model/provider/temperature wiring lives — swapping a model is an
edit there, not a rewrite of any stage.
"""
