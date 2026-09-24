"""The one tool the ledger chat agent gets: run a read-only SQL query.

The REAL enforcement that this can never write is the connection itself
(research.ledger.db.get_readonly_connection — PRAGMA query_only = ON).
`_looks_like_a_write` below is deliberately NOT that enforcement — it is a
cheap, best-effort check that turns an obviously-wrong query into a fast,
legible tool error instead of a raw SQLite exception the model has to
puzzle over. It can be wrong in both directions (a write disguised as a
CTE would slip past it; a legitimate SELECT with the word "update" in a
string literal would not) and that is fine, because the connection-level
block is what actually matters and always fires regardless.

Every result is row-capped (MAX_ROWS) before it ever reaches the model —
"the agent queries and narrates; it never receives bulk rows dumped into
context" (PHASE-PROMPTS.md phase 7). A truncated result says so, so the
model can narrow its own next query instead of asserting completeness
over data it didn't actually see.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

MAX_ROWS = 50

_WRITE_LEADING_KEYWORDS = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|REPLACE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX)\b",
    re.IGNORECASE,
)

LEDGER_SCHEMA_REFERENCE = """\
Tables (read-only):

hypotheses — one row per candidate strategy ever tested. Key columns:
  id (TEXT, the row id — cite this), ir_hash, ir_json, created_at,
  origin_anomaly_id, regime_label, mechanism, falsifier,
  model_name, model_version, prompt_hash, temperature,
  data_range, param_grid (JSON), n_variants_swept,
  gate_results (JSON array of {gate, pass, measured, threshold}),
  first_failed_gate (NULL = cleared every gate),
  sharpe_is, sharpe_oos, sharpe_oos_per_trade, sharpe_decay, sharpe_deflated,
  sr_cutoff_applied, trial_count_at_test,
  trades_total, weeks_meeting_floor, max_gap_days, weekly_histogram (JSON),
  cost_per_trade, edge_cost_ratio,
  block_results (JSON), sharpe_with_releases, sharpe_without_releases,
  combine_payout_prob,
  ensemble_id, holdout_touched, outcome ('rejected'|'blended'|'promoted'|'retired')

  Instrument is NOT a column — ir_json contains "instruments": [...], always
  exactly ["NQ","ES","GC"] pooled (json_each / LIKE against ir_json is how
  you'd filter by instrument, though every row trades all three).

  Gate numbers (first_failed_gate / gate_results[].gate):
  0 ir_validity, 1 frequency_floor, 2 cost_survival, 3 parameter_plateau,
  4 walk_forward, 5 sharpe_floor, 6 deflated_sharpe_pbo, 7 sharpe_ceiling,
  8 combine_simulator, 9 teachability.

ensembles — id, family, created_at, member_count, weights_json, sharpe_oos,
  combine_payout_prob, status ('active'|'retired'). What the trainer teaches.

drill_sessions / drill_decisions — trainer replay history (rarely relevant
  to ledger questions about candidates).

loop_state — single row (id=1): loop_pbo, loop_pbo_computed_at, updated_at.

Views (already aggregated — prefer these over hand-rolled GROUP BY):
  v_rejections_by_gate(gate, n, first_seen, last_seen)
  v_trial_budget(year, trials_consumed, hypotheses_logged)
  v_decay_distribution(decay_bucket, n)
  v_near_misses(id, created_at, sharpe_oos, combine_payout_prob, mechanism)
    — first_failed_gate = 8: cleared gate 6, died at the combine simulator
  v_holdout_usage(touches_used, last_touch)

hypotheses is append-only (a DB trigger enforces it) and this connection
is read-only regardless — SELECT only.
"""

QUERY_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_ledger",
        "description": (
            "Run a read-only SQL SELECT against the research ledger (or one of its "
            "canned views) and get back up to 50 rows. Use this to look anything up — "
            "never guess at row ids, counts, or gate outcomes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "A single SELECT statement (or WITH ... SELECT). No semicolon-separated multi-statements.",
                },
            },
            "required": ["sql"],
        },
    },
}


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool
    row_count_returned: int
    error: str | None = None

    def to_tool_content(self) -> str:
        """Compact text for the tool-result message — not a raw dump; the
        model reads this, it doesn't get the Python object."""
        if self.error:
            return f"ERROR: {self.error}"
        if not self.rows:
            return "0 rows."
        lines = [f"{self.row_count_returned} row(s){' (truncated — narrow your query)' if self.truncated else ''}:"]
        for row in self.rows:
            lines.append(str(row))
        return "\n".join(lines)


def _looks_like_a_write(sql: str) -> bool:
    return bool(_WRITE_LEADING_KEYWORDS.match(sql)) or ";" in sql.strip().rstrip(";")


def run_query(conn: sqlite3.Connection, sql: str, *, max_rows: int = MAX_ROWS) -> QueryResult:
    if _looks_like_a_write(sql):
        return QueryResult(
            columns=[], rows=[], truncated=False, row_count_returned=0,
            error="only a single read-only SELECT (or WITH ... SELECT) statement is allowed",
        )
    try:
        cur = conn.execute(sql)
        fetched = cur.fetchmany(max_rows + 1)
    except sqlite3.OperationalError as exc:
        # Covers both a genuine SQL error AND the connection-level write
        # block firing (query_only=ON) if a write slipped past the
        # best-effort check above — either way, this is what the model sees.
        return QueryResult(columns=[], rows=[], truncated=False, row_count_returned=0, error=str(exc))

    columns = [d[0] for d in cur.description] if cur.description else []
    truncated = len(fetched) > max_rows
    rows = [dict(r) for r in fetched[:max_rows]]
    return QueryResult(columns=columns, rows=rows, truncated=truncated, row_count_returned=len(rows))
