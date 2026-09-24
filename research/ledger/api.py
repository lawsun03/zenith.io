"""Thin API over the `hypotheses` table and the canned views in ledger.sql.

Deliberately thin: this module inserts and reads rows. It does not decide
gate pass/fail, does not compute the Sharpe cutoff, and does not decide
what "trial count at test time" should be — those are gate/loop concerns
(phases 4 and 5) and stay out of this file. Statistical functions live in
research.stats; this module only persists their inputs and outputs.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from research.stats.budget import ANNUAL_HYPOTHESIS_CAP, enforce_trial_budget
from research.stats.ir_hash import canonical_json, ir_hash as compute_ir_hash

# Columns the append-only trigger (hypotheses_no_rewrite in ledger.sql)
# protects. Kept here only as a comment for readers of this file — the
# trigger itself is the enforcement, not this list.
_IMMUTABLE_COLUMNS = (
    "ir_hash", "ir_json", "created_at", "model_name", "model_version",
    "prompt_hash", "n_variants_swept", "trial_count_at_test",
)


def isoformat_utc(ts: datetime) -> str:
    """ISO8601 UTC with microseconds always present (timespec="microseconds").

    `trial_count_at` compares this column as a string (`created_at <= ?`).
    `datetime.isoformat()` omits the fractional-second field entirely when
    microsecond == 0, which breaks lexicographic ordering against rows that
    do have one (e.g. "...T00:00:00Z" sorts *after* "...T00:00:00.5Z", not
    before, because '.' < 'Z' in ASCII) — a fixed-width format is what
    makes string comparison agree with chronological order.

    A naive `ts` (no tzinfo) is treated as already-UTC rather than passed to
    `astimezone`, which would otherwise interpret it as local system time.

    Public (not `_`-prefixed): research.trainer.sampling keys decisions by
    this same canonical string, so a decision's ledger row and its
    in-memory Candidate.key must serialize `ts` identically or "was this
    gotten wrong before" would silently never match.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def append_hypothesis(
    conn: sqlite3.Connection,
    *,
    ir: dict[str, Any],
    mechanism: str,
    falsifier: str,
    model_name: str,
    model_version: str,
    prompt_hash: str,
    temperature: float,
    data_range: str,
    param_grid: dict[str, Any],
    n_variants_swept: int,
    trial_count_at_test: int,
    origin_anomaly_id: str | None = None,
    regime_label: str | None = None,
    regime_label_confidence: float | None = None,
    created_at: datetime | None = None,
    hypothesis_id: str | None = None,
    cap: int = ANNUAL_HYPOTHESIS_CAP,
) -> str:
    """Insert one row into `hypotheses`. Returns the new row's id.

    `ir_hash` and `ir_json` are derived here from `ir` via
    research.stats.ir_hash, so the stored hash is always the canonical one —
    callers never construct it by hand. `gate_results` starts empty and
    `outcome` starts 'rejected'; both are filled in later via
    `record_gate_result` and a direct outcome update once gates run.

    Raises research.stats.budget.TrialBudgetExceeded if the calendar year
    of `created_at` has already logged `cap` hypotheses — this is the loop's
    hard stop (CLAUDE.md rule 6), not a warning.
    """
    ts = created_at or datetime.now(timezone.utc)
    created_at_iso = isoformat_utc(ts)

    enforce_trial_budget(conn, ts.year, cap=cap)

    row_id = hypothesis_id or str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO hypotheses (
            id, ir_hash, ir_json, created_at,
            origin_anomaly_id, regime_label, regime_label_confidence,
            mechanism, falsifier,
            model_name, model_version, prompt_hash, temperature,
            data_range, param_grid, n_variants_swept,
            gate_results, first_failed_gate,
            trial_count_at_test,
            holdout_touched, outcome
        ) VALUES (
            :id, :ir_hash, :ir_json, :created_at,
            :origin_anomaly_id, :regime_label, :regime_label_confidence,
            :mechanism, :falsifier,
            :model_name, :model_version, :prompt_hash, :temperature,
            :data_range, :param_grid, :n_variants_swept,
            :gate_results, :first_failed_gate,
            :trial_count_at_test,
            0, 'rejected'
        )
        """,
        {
            "id": row_id,
            "ir_hash": compute_ir_hash(ir),
            "ir_json": canonical_json(ir),
            "created_at": created_at_iso,
            "origin_anomaly_id": origin_anomaly_id,
            "regime_label": regime_label,
            "regime_label_confidence": regime_label_confidence,
            "mechanism": mechanism,
            "falsifier": falsifier,
            "model_name": model_name,
            "model_version": model_version,
            "prompt_hash": prompt_hash,
            "temperature": temperature,
            "data_range": data_range,
            "param_grid": canonical_json(param_grid),
            "n_variants_swept": n_variants_swept,
            "gate_results": "[]",
            "first_failed_gate": None,
            "trial_count_at_test": trial_count_at_test,
        },
    )
    conn.commit()
    return row_id


def record_gate_result(
    conn: sqlite3.Connection,
    hypothesis_id: str,
    gate: int,
    passed: bool,
    measured: float | None = None,
    threshold: float | None = None,
) -> None:
    """Record one gate's outcome for `hypothesis_id`.

    `gate_results` and `first_failed_gate` are not protected by the
    append-only trigger (see ledger.sql: only provenance and trial-count
    columns are immutable), so this updates the existing row rather than
    inserting a new one.

    Idempotent per gate number: re-recording gate N replaces its previous
    entry instead of appending a duplicate, so re-running an evaluation
    doesn't corrupt the history. `first_failed_gate` is always recomputed
    from the full set of recorded results, as min(gate) among failures.
    """
    row = conn.execute(
        "SELECT gate_results FROM hypotheses WHERE id = ?", (hypothesis_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no hypothesis with id {hypothesis_id!r}")

    results: list[dict[str, Any]] = json.loads(row["gate_results"])
    results = [r for r in results if r["gate"] != gate]
    results.append(
        {"gate": gate, "pass": passed, "measured": measured, "threshold": threshold}
    )
    results.sort(key=lambda r: r["gate"])

    failed_gates = [r["gate"] for r in results if not r["pass"]]
    first_failed_gate = min(failed_gates) if failed_gates else None

    conn.execute(
        "UPDATE hypotheses SET gate_results = ?, first_failed_gate = ? WHERE id = ?",
        (json.dumps(results), first_failed_gate, hypothesis_id),
    )
    conn.commit()


def record_gate_results(
    conn: sqlite3.Connection, hypothesis_id: str, results: list[dict[str, Any]],
) -> None:
    """Record a full gate battery in one call — the phase-4 gate pipeline's
    entry point into the ledger (research.gates.pipeline). Each result is
    `{"gate": int, "passed": bool, "measured": float|None, "threshold": float|None}`,
    exactly research.gates.types.GateResult's shape, for EVERY gate reached
    or not (research.gates.types.unreached) — record_gate_result is called
    per-gate so the append-only trigger's column list and its idempotent
    per-gate replace semantics apply uniformly whether this is gate 0 of a
    fresh evaluation or a re-run overwriting a stale one.
    """
    for r in results:
        record_gate_result(conn, hypothesis_id, r["gate"], r["passed"], r["measured"], r["threshold"])


# Columns record_candidate_scores is allowed to touch — every one of them is
# a performance/robustness/disposition field the append-only trigger leaves
# mutable (ledger.sql: "Gate results and disposition are the ONLY mutable
# fields"). Listed explicitly so an unknown kwarg fails loud instead of
# silently building `UPDATE ... SET typo = ?` for a column that doesn't exist.
_SCORE_COLUMNS = frozenset({
    "sharpe_is", "sharpe_oos", "sharpe_oos_per_trade", "sharpe_decay", "sharpe_deflated",
    "sr_cutoff_applied",
    "trades_total", "weeks_meeting_floor", "max_gap_days", "weekly_histogram",
    "cost_per_trade", "edge_cost_ratio", "block_results",
    "sharpe_with_releases", "sharpe_without_releases", "combine_payout_prob",
    "ensemble_id", "holdout_touched", "outcome",
})


def record_candidate_scores(conn: sqlite3.Connection, hypothesis_id: str, **fields: Any) -> None:
    """Update the performance/robustness/disposition columns computed by the
    gate pipeline (research.gates.pipeline) for an existing row. JSON-typed
    columns (`weekly_histogram`, `block_results`) accept a Python dict/list
    and are serialized here; everything else is passed through as-is.

    Raises ValueError on any field name outside `_SCORE_COLUMNS` — these
    columns are the only ones this function may ever touch (provenance and
    trial count are enforced immutable by the DB trigger itself; gate
    results go through record_gate_results, not this function).
    """
    unknown = set(fields) - _SCORE_COLUMNS
    if unknown:
        raise ValueError(f"record_candidate_scores: unknown column(s) {sorted(unknown)}")
    if not fields:
        return

    for json_col in ("weekly_histogram", "block_results"):
        if json_col in fields and fields[json_col] is not None:
            fields[json_col] = json.dumps(fields[json_col])

    set_clause = ", ".join(f"{col} = :{col}" for col in fields)
    fields["id"] = hypothesis_id
    cur = conn.execute(f"UPDATE hypotheses SET {set_clause} WHERE id = :id", fields)
    if cur.rowcount == 0:
        raise KeyError(f"no hypothesis with id {hypothesis_id!r}")
    conn.commit()


def insert_ensemble(
    conn: sqlite3.Connection, ensemble: Any,  # research.gates.ensemble.Ensemble
    *, created_at: datetime | None = None,
    sharpe_oos: float | None = None, combine_payout_prob: float | None = None,
    ensemble_id: str | None = None,
) -> str:
    """Persist an equal-weighted ensemble (research.gates.ensemble.build_ensemble)
    and mark every member hypothesis `outcome = 'blended'`, `ensemble_id =`
    the new row. Weights are read straight off `ensemble.members` — this
    function does not compute them, so it cannot silently re-introduce a
    selection step that `build_ensemble` itself was written to make
    impossible.
    """
    row_id = ensemble_id or str(uuid.uuid4())
    created_at_iso = isoformat_utc(created_at or datetime.now(timezone.utc))
    weights = {m.hypothesis_id: str(m.weight) for m in ensemble.members}

    conn.execute(
        """
        INSERT INTO ensembles (id, family, created_at, member_count, weights_json,
                                sharpe_oos, combine_payout_prob, status)
        VALUES (:id, :family, :created_at, :member_count, :weights_json,
                :sharpe_oos, :combine_payout_prob, 'active')
        """,
        {
            "id": row_id, "family": ensemble.family, "created_at": created_at_iso,
            "member_count": ensemble.member_count, "weights_json": json.dumps(weights),
            "sharpe_oos": sharpe_oos, "combine_payout_prob": combine_payout_prob,
        },
    )
    for member in ensemble.members:
        record_candidate_scores(conn, member.hypothesis_id, ensemble_id=row_id, outcome="blended")
    conn.commit()
    return row_id


def get_ensemble(conn: sqlite3.Connection, ensemble_id: str) -> Any:  # research.gates.ensemble.Ensemble
    """Reconstruct an Ensemble from its ledger rows — the read side
    `insert_ensemble` never had. Weights come straight from
    `weights_json` (never recomputed as 1/N here) so this can never
    silently disagree with what was actually persisted; each member's
    `ir_doc` comes from its own `hypotheses.ir_json` row, which is the
    only place it's stored.
    """
    from research.gates.ensemble import Ensemble, EnsembleMember  # avoid an import cycle at module load

    ens_row = conn.execute(
        "SELECT family, weights_json FROM ensembles WHERE id = ?", (ensemble_id,)
    ).fetchone()
    if ens_row is None:
        raise KeyError(f"no ensemble with id {ensemble_id!r}")

    weights: dict[str, str] = json.loads(ens_row["weights_json"])
    members = []
    for hyp_id, weight_str in weights.items():
        row = conn.execute("SELECT ir_json FROM hypotheses WHERE id = ?", (hyp_id,)).fetchone()
        if row is None:
            raise KeyError(f"ensemble {ensemble_id!r} member {hyp_id!r} missing from hypotheses")
        members.append(EnsembleMember(hypothesis_id=hyp_id, ir_doc=json.loads(row["ir_json"]),
                                       weight=Decimal(weight_str)))
    return Ensemble(family=ens_row["family"], members=tuple(members))


def list_active_ensembles(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Ensembles a trainer session can be started against. A retired
    ensemble isn't something to drill — it's no longer what gets taught."""
    rows = conn.execute(
        "SELECT id, family, created_at, member_count, sharpe_oos, combine_payout_prob "
        "FROM ensembles WHERE status = 'active' ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def insert_drill_session(
    conn: sqlite3.Connection,
    *,
    ensemble_id: str,
    n_decisions: int,
    setups_correctly_taken: int,
    setups_missed: int,
    false_positives: int,
    direction_errors: int,
    stop_placement_errors: int,
    fidelity_score: float,
    outcome_pnl_shadow: str | None = None,
    notes: str | None = None,
    started_at: datetime | None = None,
    session_id: str | None = None,
) -> str:
    """Insert one finished drill session (research.trainer.scoring.tally_session's
    output). `fidelity_score` is the number the trend chart plots;
    `outcome_pnl_shadow` is recorded and never read back for scoring."""
    row_id = session_id or str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO drill_sessions (
            id, ensemble_id, started_at, n_decisions,
            setups_correctly_taken, setups_missed, false_positives,
            direction_errors, stop_placement_errors,
            fidelity_score, outcome_pnl_shadow, notes
        ) VALUES (
            :id, :ensemble_id, :started_at, :n_decisions,
            :setups_correctly_taken, :setups_missed, :false_positives,
            :direction_errors, :stop_placement_errors,
            :fidelity_score, :outcome_pnl_shadow, :notes
        )
        """,
        {
            "id": row_id, "ensemble_id": ensemble_id,
            "started_at": isoformat_utc(started_at or datetime.now(timezone.utc)),
            "n_decisions": n_decisions,
            "setups_correctly_taken": setups_correctly_taken,
            "setups_missed": setups_missed,
            "false_positives": false_positives,
            "direction_errors": direction_errors,
            "stop_placement_errors": stop_placement_errors,
            "fidelity_score": fidelity_score,
            "outcome_pnl_shadow": outcome_pnl_shadow,
            "notes": notes,
        },
    )
    conn.commit()
    return row_id


def insert_drill_decision(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    hypothesis_id: str,
    instrument: str,
    decision_ts: datetime,
    ir_fired: bool,
    near_miss: bool,
    user_is_setup: bool,
    correct_setup: bool,
    regime_label: str | None = None,
    ir_side: str | None = None,
    ir_stop_price: str | None = None,
    user_direction: str | None = None,
    user_stop_price: str | None = None,
    correct_direction: bool | None = None,
    correct_stop: bool | None = None,
    outcome_pnl_shadow: str | None = None,
    decision_id: str | None = None,
) -> str:
    """Insert one scored decision point. `decision_ts` is stored via
    isoformat_utc — the exact canonical form research.trainer.sampling's
    Candidate.key must also use, or "gotten wrong before" lookups silently
    never match. correct_direction/correct_stop are stored as-is (None
    stays NULL — "not applicable", never coerced to a false)."""
    row_id = decision_id or str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO drill_decisions (
            id, session_id, hypothesis_id, instrument, decision_ts, regime_label,
            ir_fired, near_miss, ir_side, ir_stop_price,
            user_is_setup, user_direction, user_stop_price,
            correct_setup, correct_direction, correct_stop, outcome_pnl_shadow
        ) VALUES (
            :id, :session_id, :hypothesis_id, :instrument, :decision_ts, :regime_label,
            :ir_fired, :near_miss, :ir_side, :ir_stop_price,
            :user_is_setup, :user_direction, :user_stop_price,
            :correct_setup, :correct_direction, :correct_stop, :outcome_pnl_shadow
        )
        """,
        {
            "id": row_id, "session_id": session_id, "hypothesis_id": hypothesis_id,
            "instrument": instrument, "decision_ts": isoformat_utc(decision_ts),
            "regime_label": regime_label,
            "ir_fired": int(ir_fired), "near_miss": int(near_miss),
            "ir_side": ir_side, "ir_stop_price": ir_stop_price,
            "user_is_setup": int(user_is_setup), "user_direction": user_direction,
            "user_stop_price": user_stop_price,
            "correct_setup": int(correct_setup),
            "correct_direction": None if correct_direction is None else int(correct_direction),
            "correct_stop": None if correct_stop is None else int(correct_stop),
            "outcome_pnl_shadow": outcome_pnl_shadow,
        },
    )
    conn.commit()
    return row_id


def list_drill_sessions(conn: sqlite3.Connection, ensemble_id: str) -> list[dict[str, Any]]:
    """Past sessions for one ensemble, oldest first — the fidelity trend
    the trainer's sparkline plots (PHASE-PROMPTS.md Phase 6, acceptance
    test: "a fidelity score, and it trends across sessions")."""
    rows = conn.execute(
        "SELECT * FROM drill_sessions WHERE ensemble_id = ? ORDER BY started_at",
        (ensemble_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def previously_wrong_decisions(conn: sqlite3.Connection, ensemble_id: str) -> set[tuple[str, str, str]]:
    """(hypothesis_id, instrument, decision_ts) for every decision any past
    session against this ensemble got wrong (any of the three sub-
    answers) — exactly the key shape research.trainer.sampling.Candidate.key
    produces, and what its weighting looks up."""
    rows = conn.execute(
        """
        SELECT DISTINCT dd.hypothesis_id, dd.instrument, dd.decision_ts
        FROM drill_decisions dd
        JOIN drill_sessions ds ON ds.id = dd.session_id
        WHERE ds.ensemble_id = ?
          AND (dd.correct_setup = 0 OR dd.correct_direction = 0 OR dd.correct_stop = 0)
        """,
        (ensemble_id,),
    ).fetchall()
    return {(r["hypothesis_id"], r["instrument"], r["decision_ts"]) for r in rows}


def trial_count_at(conn: sqlite3.Connection, timestamp: datetime) -> int:
    """Sum of n_variants_swept for every row created at or before `timestamp`.

    This is the trial count gates 5 and 6 read the Sharpe cutoff against
    (docs/research-loop/gates.md) — CLAUDE.md rule 7 is explicit that it is
    a sum weighted by n_variants_swept, not a row count (that's
    research.stats.budget.count_hypotheses_in_year, a different number).
    """
    ts_iso = isoformat_utc(timestamp)
    row = conn.execute(
        "SELECT COALESCE(SUM(n_variants_swept), 0) FROM hypotheses WHERE created_at <= ?",
        (ts_iso,),
    ).fetchone()
    return row[0]


def per_trade_oos_sharpes(conn: sqlite3.Connection, timestamp: datetime) -> list[float]:
    """Every recorded `sharpe_oos_per_trade` (non-annualized — see
    ledger.sql's column comment) at or before `timestamp`, oldest scored
    trials first come first. This is the input
    research.stats.deflated_sharpe.empirical_sr_variance needs for gate 6's
    V[{SR_n}] once enough trials have been scored (research.loop.gate_runner
    decides the 20-trial cutover, not this function — it only reads)."""
    ts_iso = isoformat_utc(timestamp)
    rows = conn.execute(
        "SELECT sharpe_oos_per_trade FROM hypotheses "
        "WHERE created_at <= ? AND sharpe_oos_per_trade IS NOT NULL "
        "ORDER BY created_at",
        (ts_iso,),
    ).fetchall()
    return [r[0] for r in rows]


def record_loop_pbo(conn: sqlite3.Connection, loop_pbo: float, *, computed_at: datetime | None = None) -> None:
    """Upsert the single loop_state row with the most recently computed
    loop-level PBO (research.loop.gate_runner.compute_loop_pbo). Not
    append-only — this is current STATE, not a history of trials, and
    ledger.sql gives it no triggers restricting it. research.loop.cycle
    calls this once per cycle, whenever that cycle computed a real
    (non-None) loop_pbo."""
    ts_iso = isoformat_utc(computed_at or datetime.now(timezone.utc))
    conn.execute(
        """
        INSERT INTO loop_state (id, loop_pbo, loop_pbo_computed_at, updated_at)
        VALUES (1, :loop_pbo, :computed_at, :computed_at)
        ON CONFLICT(id) DO UPDATE SET
            loop_pbo = :loop_pbo, loop_pbo_computed_at = :computed_at, updated_at = :computed_at
        """,
        {"loop_pbo": loop_pbo, "computed_at": ts_iso},
    )
    conn.commit()


def get_loop_pbo(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The most recently recorded loop-level PBO, or None if no cycle has
    ever computed one (e.g. a fresh ledger, or every cycle so far only had
    a single candidate — compute_loop_pbo needs at least 2)."""
    row = conn.execute(
        "SELECT loop_pbo, loop_pbo_computed_at FROM loop_state WHERE id = 1"
    ).fetchone()
    if row is None or row["loop_pbo"] is None:
        return None
    return dict(row)


# --- canned view queries -----------------------------------------------
# Thin wrappers so callers don't hand-write `SELECT * FROM v_...`. The
# views themselves are defined in ledger.sql so they can't drift from the
# tables (ledger.sql comment: "Views, so they cannot drift from the tables").

def _select_all(conn: sqlite3.Connection, view: str) -> list[dict[str, Any]]:
    rows = conn.execute(f"SELECT * FROM {view}").fetchall()
    return [dict(r) for r in rows]


def rejections_by_gate(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return _select_all(conn, "v_rejections_by_gate")


def trial_budget_by_year(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return _select_all(conn, "v_trial_budget")


def decay_distribution(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return _select_all(conn, "v_decay_distribution")


def near_misses(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return _select_all(conn, "v_near_misses")


def holdout_usage(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return _select_all(conn, "v_holdout_usage")
