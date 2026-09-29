"""Research ledger — SQLite store for the protocol spec §10 tables.

One local file (default research/ledger.db, gitignored), same pattern as
app/sync/outbox.py. Writers so far: `protocols` (step 3), `holdout_evals`,
`families`, `variants` (step 4). The other tables exist so later steps add
rows rather than schema.

Append-only rules live in the database as triggers, not in callers:
- a locked protocol's hash and YAML can never change (new version instead);
- trials and holdout_evals can never be deleted (§4: every trial counts);
- a decided holdout eval can't be edited, a burned holdout can't be
  un-burned and the holdout counter only goes up;
- at most one pending-or-evaluated holdout eval per (strategy, protocol).
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_LEDGER_PATH = Path("research/ledger.db")

_PROTOCOL_HOLDOUT_TRIGGERS = """
CREATE TRIGGER protocols_burned_is_final BEFORE UPDATE OF holdout_status ON protocols
WHEN OLD.holdout_status = 'burned' AND NEW.holdout_status != 'burned'
BEGIN SELECT RAISE(ABORT, 'a burned holdout stays burned; lock a new protocol version'); END;
CREATE TRIGGER protocols_evals_used_monotonic BEFORE UPDATE OF holdout_evals_used ON protocols
WHEN NEW.holdout_evals_used < OLD.holdout_evals_used
BEGIN SELECT RAISE(ABORT, 'holdout_evals_used never decreases'); END;
"""

# One row per call (spec §3): pending agent requests, refusals, declines and
# evaluations. The partial unique index is the database backstop for the
# one-shot rule; refused and declined rows don't hold the slot.
_HOLDOUT_EVALS = """
CREATE TABLE holdout_evals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_hash TEXT NOT NULL,
    protocol_id   TEXT NOT NULL REFERENCES protocols(id),
    requested_by  TEXT NOT NULL,
    caller_kind   TEXT NOT NULL CHECK (caller_kind IN ('human','agent')),
    model_id      TEXT,
    ts            TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('pending','evaluated','refused','declined')),
    reason        TEXT,
    approved_by   TEXT,
    decided_at    TEXT,
    metrics_json  TEXT,
    passed        INTEGER,
    CHECK (status != 'evaluated' OR (approved_by IS NOT NULL AND metrics_json IS NOT NULL))
);
CREATE UNIQUE INDEX holdout_evals_one_shot ON holdout_evals (strategy_hash, protocol_id)
    WHERE status IN ('pending','evaluated');
CREATE TRIGGER holdout_evals_no_delete BEFORE DELETE ON holdout_evals
BEGIN SELECT RAISE(ABORT, 'holdout evaluations are never deleted'); END;
CREATE TRIGGER holdout_evals_decided_immutable BEFORE UPDATE ON holdout_evals
WHEN OLD.status != 'pending'
BEGIN SELECT RAISE(ABORT, 'a decided holdout evaluation is immutable'); END;
"""

_SCHEMA = """
CREATE TABLE protocols (
    id                 TEXT PRIMARY KEY,
    hash               TEXT NOT NULL,
    yaml               TEXT NOT NULL,
    locked_at          TEXT NOT NULL,
    locked_by          TEXT NOT NULL,
    holdout_status     TEXT NOT NULL CHECK (holdout_status IN ('sealed','partially_used','burned')),
    holdout_evals_used INTEGER NOT NULL DEFAULT 0
);
CREATE TRIGGER protocols_immutable BEFORE UPDATE OF id, hash, yaml, locked_at ON protocols
BEGIN SELECT RAISE(ABORT, 'locked protocol is immutable; create a new protocol version'); END;
CREATE TRIGGER protocols_no_delete BEFORE DELETE ON protocols
BEGIN SELECT RAISE(ABORT, 'locked protocols cannot be deleted'); END;
""" + _PROTOCOL_HOLDOUT_TRIGGERS + """
CREATE TABLE families (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('user','paper','book','video','derived')),
    source_ref  TEXT NOT NULL CHECK (length(trim(source_ref)) > 0),
    hypothesis  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    protocol_id TEXT NOT NULL REFERENCES protocols(id),
    holdout_contaminated INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE variants (
    id            TEXT PRIMARY KEY,
    family_id     TEXT NOT NULL REFERENCES families(id),
    strategy_hash TEXT NOT NULL UNIQUE,
    params_json   TEXT NOT NULL,
    engine_commit TEXT NOT NULL,
    code_hash     TEXT NOT NULL,
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE trials (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    variant_id   TEXT NOT NULL REFERENCES variants(id),
    instrument   TEXT NOT NULL,
    period       TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    null_pct     REAL,
    dsr          REAL,
    fdr_pass     INTEGER,
    passed_dev   INTEGER
);
CREATE TRIGGER trials_no_delete BEFORE DELETE ON trials
BEGIN SELECT RAISE(ABORT, 'trials are never deleted (spec §4)'); END;

""" + _HOLDOUT_EVALS + """
CREATE TABLE graduations (
    strategy_hash        TEXT PRIMARY KEY,
    approved_by          TEXT NOT NULL,
    ts                   TEXT NOT NULL,
    rules_card_path      TEXT NOT NULL,
    holdout_contaminated INTEGER NOT NULL
);

CREATE TABLE forward_trades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_hash  TEXT NOT NULL,
    date           TEXT NOT NULL,
    side           TEXT NOT NULL CHECK (side IN ('long','short')),
    taken          INTEGER NOT NULL,
    entry          TEXT,
    stop           TEXT,
    target         TEXT,
    exit           TEXT,
    exit_reason    TEXT,
    contracts      INTEGER,
    commission_usd TEXT,
    rules_followed INTEGER,
    notes          TEXT
);

CREATE TABLE decay_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_hash TEXT NOT NULL,
    ts            TEXT NOT NULL,
    type          TEXT NOT NULL,
    detail        TEXT
);
"""

# v1 (step 3) had no writers for families/variants/holdout_evals, so v1 rows
# there can only be hand-made; they are carried over, not interpreted.
_MIGRATE_1_TO_2 = """
ALTER TABLE families ADD COLUMN holdout_contaminated INTEGER NOT NULL DEFAULT 0;
ALTER TABLE variants ADD COLUMN code_hash TEXT NOT NULL DEFAULT '';
DROP TRIGGER holdout_evals_no_delete;
ALTER TABLE holdout_evals RENAME TO holdout_evals_v1;
""" + _HOLDOUT_EVALS + """
INSERT INTO holdout_evals (strategy_hash, protocol_id, requested_by, caller_kind, ts, status,
                           approved_by, decided_at, metrics_json, passed)
SELECT strategy_hash, protocol_id, requested_by, 'human', ts,
       CASE WHEN metrics_json IS NULL THEN 'pending' ELSE 'evaluated' END,
       approved_by, CASE WHEN metrics_json IS NULL THEN NULL ELSE ts END, metrics_json, passed
FROM holdout_evals_v1;
DROP TABLE holdout_evals_v1;
""" + _PROTOCOL_HOLDOUT_TRIGGERS


class Ledger:
    SCHEMA_VERSION = 2

    def __init__(self, db_path: str | Path = DEFAULT_LEDGER_PATH) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        current = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if current == 0:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")
        elif current == 1:
            self._conn.executescript("BEGIN;" + _MIGRATE_1_TO_2 + "PRAGMA user_version = 2; COMMIT;")
            log.info("ledger %s migrated schema v1 -> v2", self.path)
        elif current != self.SCHEMA_VERSION:
            raise RuntimeError(f"ledger {self.path} is schema v{current}; "
                               f"expected v{self.SCHEMA_VERSION}. Migration not implemented.")

    def close(self) -> None:
        self._conn.close()

    def get_protocol(self, protocol_id: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM protocols WHERE id = ?",
                                  (protocol_id,)).fetchone()

    def insert_protocol(self, protocol_id: str, hash_: str, yaml_text: str,
                        locked_by: str, holdout_status: str) -> None:
        self._conn.execute(
            "INSERT INTO protocols (id, hash, yaml, locked_at, locked_by, holdout_status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (protocol_id, hash_, yaml_text, datetime.now(timezone.utc).isoformat(),
             locked_by, holdout_status),
        )

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    @contextmanager
    def tx(self):
        """BEGIN IMMEDIATE: read-check-write sequences (the burn counter) can't
        interleave with another process holding the same ledger."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")
