"""Phase 3: anomaly detection and Grok regime labelling.

Pipeline (docs/research-loop/README.md):
  bars -> features.compute_session_features -> ranking.rank_anomalies
       -> pipeline.label_sessions (Grok, top ~10%, budget-capped)
       -> the anomaly table

`regime_label` here is classification metadata only — CLAUDE.md domain
invariant #5. It is written to the anomaly table and, later, to the
ledger's `hypotheses.regime_label` column (phase 5), and it is used to
include/exclude or group sessions for validation. research.ir.schema
already bans the literal string "regime_label" anywhere in an IR document;
research.anomaly.labels.REGIME_LEDGER_FIELD names that same string so the
two modules cannot drift apart silently.
"""
