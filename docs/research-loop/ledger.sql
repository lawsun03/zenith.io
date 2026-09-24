-- Research ledger. APPEND-ONLY.
--
-- Nothing deletes from `hypotheses`. The row count (weighted by n_variants_swept) IS the
-- trial count that gates 5 and 6 depend on. Deleting a row silently invalidates every
-- subsequent significance calculation.
--
-- Enforce append-only with triggers below, not with discipline.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS hypotheses (
    id                       TEXT PRIMARY KEY,           -- uuid4
    ir_hash                  TEXT NOT NULL UNIQUE,       -- sha256 of canonical (key-sorted) IR
    ir_json                  TEXT NOT NULL,
    created_at               TEXT NOT NULL,              -- ISO8601 UTC

    -- provenance: where the idea came from
    origin_anomaly_id        TEXT,
    regime_label             TEXT,                       -- Grok's label on the origin session
    regime_label_confidence  REAL,
    mechanism                TEXT NOT NULL,              -- Astra's causal story: why this SHOULD work
    falsifier                TEXT NOT NULL,              -- what Kimi's review said would disprove it

    -- provenance: which model, exactly
    model_name               TEXT NOT NULL,
    model_version            TEXT NOT NULL,
    prompt_hash              TEXT NOT NULL,              -- sha256 of the prompt template
    temperature              REAL NOT NULL,

    -- what was tested
    data_range               TEXT NOT NULL,              -- "2010-06-06/2024-12-31"
    param_grid               TEXT NOT NULL,              -- JSON of every combination swept
    n_variants_swept         INTEGER NOT NULL,           -- counts toward the trial budget

    -- gate outcomes
    gate_results             TEXT NOT NULL,              -- JSON [{gate, pass, measured, threshold}]
    first_failed_gate        INTEGER,                    -- NULL = cleared everything

    -- performance
    sharpe_is                REAL,
    sharpe_oos               REAL,
    sharpe_decay             REAL,                       -- (is - oos) / is
    sharpe_deflated          REAL,
    sr_cutoff_applied        REAL,                       -- the cutoff in force at test time
    trial_count_at_test      INTEGER NOT NULL,           -- reproducibility of the cutoff

    -- frequency floor
    trades_total             INTEGER,
    weeks_meeting_floor      REAL,
    max_gap_days             INTEGER,
    weekly_histogram         TEXT,                       -- JSON

    -- costs
    cost_per_trade           TEXT,                       -- Decimal as string, never float
    edge_cost_ratio          REAL,

    -- robustness
    block_results            TEXT,                       -- JSON {"2010-13":0.4,"2014-17":0.3,...}
    sharpe_with_releases     REAL,
    sharpe_without_releases  REAL,                       -- gate scores on min() of these two

    -- prop account
    combine_payout_prob      REAL,

    -- disposition
    ensemble_id              TEXT REFERENCES ensembles(id),
    holdout_touched          INTEGER NOT NULL DEFAULT 0,
    outcome                  TEXT NOT NULL               -- rejected|blended|promoted|retired
                             CHECK (outcome IN ('rejected','blended','promoted','retired'))
);

CREATE INDEX IF NOT EXISTS idx_hyp_created     ON hypotheses(created_at);
CREATE INDEX IF NOT EXISTS idx_hyp_failed_gate ON hypotheses(first_failed_gate);
CREATE INDEX IF NOT EXISTS idx_hyp_outcome     ON hypotheses(outcome);
CREATE INDEX IF NOT EXISTS idx_hyp_ensemble    ON hypotheses(ensemble_id);

-- Append-only enforcement. Gate results and disposition are the ONLY mutable fields,
-- and only from NULL/'rejected' forward.
CREATE TRIGGER IF NOT EXISTS hypotheses_no_delete
BEFORE DELETE ON hypotheses
BEGIN
    SELECT RAISE(ABORT, 'hypotheses is append-only: deletion invalidates the trial count');
END;

CREATE TRIGGER IF NOT EXISTS hypotheses_no_rewrite
BEFORE UPDATE OF ir_hash, ir_json, created_at, model_name, model_version,
                 prompt_hash, n_variants_swept, trial_count_at_test
ON hypotheses
BEGIN
    SELECT RAISE(ABORT, 'immutable field: provenance and trial count cannot be rewritten');
END;


-- Ensembles: what actually gets taught. Equal weights by construction.
CREATE TABLE IF NOT EXISTS ensembles (
    id                    TEXT PRIMARY KEY,
    family                TEXT NOT NULL,          -- e.g. "sweep-displacement"
    created_at            TEXT NOT NULL,
    member_count          INTEGER NOT NULL,
    -- weights are equal by construction; store explicitly so a future change is visible
    weights_json          TEXT NOT NULL,
    sharpe_oos            REAL,
    combine_payout_prob   REAL,
    status                TEXT NOT NULL           -- active|retired
                          CHECK (status IN ('active','retired'))
);


-- Drill sessions: your trainer runs. Fidelity, not P&L.
CREATE TABLE IF NOT EXISTS drill_sessions (
    id                      TEXT PRIMARY KEY,
    ensemble_id             TEXT NOT NULL REFERENCES ensembles(id),
    started_at              TEXT NOT NULL,
    n_decisions             INTEGER NOT NULL,

    -- scored against the RULES, never against the market outcome
    setups_correctly_taken  INTEGER NOT NULL,
    setups_missed           INTEGER NOT NULL,
    false_positives         INTEGER NOT NULL,   -- took a trade the rules decline
    direction_errors        INTEGER NOT NULL,
    stop_placement_errors   INTEGER NOT NULL,

    fidelity_score          REAL NOT NULL,      -- fraction of decisions matching the IR
    -- what the market did, recorded but NEVER aggregated into fidelity_score
    outcome_pnl_shadow      TEXT,               -- Decimal as string
    notes                   TEXT                -- what you felt on the ones you got wrong
);

CREATE INDEX IF NOT EXISTS idx_drill_ensemble ON drill_sessions(ensemble_id);


-- Drill decisions: one row per decision point actually presented to a trainee.
-- drill_sessions above is session-level aggregates only — sampling that biases
-- toward "decision points you got wrong before" (PHASE-PROMPTS.md Phase 6,
-- requirement 3) needs per-decision identity, which only this table carries.
CREATE TABLE IF NOT EXISTS drill_decisions (
    id                    TEXT PRIMARY KEY,
    session_id            TEXT NOT NULL REFERENCES drill_sessions(id),
    hypothesis_id         TEXT NOT NULL REFERENCES hypotheses(id),  -- which ensemble member
    instrument            TEXT NOT NULL,
    decision_ts           TEXT NOT NULL,
    regime_label          TEXT,

    ir_fired              INTEGER NOT NULL,
    near_miss             INTEGER NOT NULL,
    ir_side               TEXT,
    ir_stop_price         TEXT,               -- Decimal as string

    user_is_setup         INTEGER NOT NULL,
    user_direction        TEXT,
    user_stop_price       TEXT,

    -- scored against the RULES, never against the market outcome
    correct_setup         INTEGER NOT NULL,
    correct_direction     INTEGER,            -- NULL when not applicable (no true-positive agreement)
    correct_stop          INTEGER,

    outcome_pnl_shadow    TEXT                -- recorded, NEVER read by scoring
);

CREATE INDEX IF NOT EXISTS idx_drill_decisions_session ON drill_decisions(session_id);
CREATE INDEX IF NOT EXISTS idx_drill_decisions_lookup
    ON drill_decisions(hypothesis_id, instrument, decision_ts);

-- Append-only, same reason as `hypotheses`: the weighted sampler reads
-- "was this gotten wrong before" from this table, and a training log that
-- can be rewritten after the fact stops meaning anything.
CREATE TRIGGER IF NOT EXISTS drill_decisions_no_delete
BEFORE DELETE ON drill_decisions
BEGIN
    SELECT RAISE(ABORT, 'drill_decisions is append-only: sampling depends on the history staying intact');
END;

CREATE TRIGGER IF NOT EXISTS drill_decisions_no_rewrite
BEFORE UPDATE ON drill_decisions
BEGIN
    SELECT RAISE(ABORT, 'drill_decisions is append-only: a training log is not editable after the fact');
END;


-- Canned aggregates the chat agent queries. Views, so they cannot drift from the tables.

CREATE VIEW IF NOT EXISTS v_rejections_by_gate AS
SELECT first_failed_gate AS gate,
       COUNT(*)          AS n,
       MIN(created_at)   AS first_seen,
       MAX(created_at)   AS last_seen
FROM hypotheses
WHERE first_failed_gate IS NOT NULL
GROUP BY first_failed_gate
ORDER BY n DESC;

CREATE VIEW IF NOT EXISTS v_trial_budget AS
SELECT strftime('%Y', created_at)   AS year,
       SUM(n_variants_swept)        AS trials_consumed,
       COUNT(*)                     AS hypotheses_logged
FROM hypotheses
GROUP BY year;

CREATE VIEW IF NOT EXISTS v_decay_distribution AS
SELECT ROUND(sharpe_decay, 1) AS decay_bucket,
       COUNT(*)               AS n
FROM hypotheses
WHERE sharpe_decay IS NOT NULL
GROUP BY decay_bucket
ORDER BY decay_bucket;

-- The population most likely to contain a fixable near-miss.
CREATE VIEW IF NOT EXISTS v_near_misses AS
SELECT id, created_at, sharpe_oos, combine_payout_prob, mechanism
FROM hypotheses
WHERE first_failed_gate = 8
ORDER BY combine_payout_prob DESC;

CREATE VIEW IF NOT EXISTS v_holdout_usage AS
SELECT COUNT(*) AS touches_used,
       MAX(created_at) AS last_touch
FROM hypotheses
WHERE holdout_touched = 1;
