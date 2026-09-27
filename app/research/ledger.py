"""Research ledger — SQLite store for the protocol spec §10 tables.

One local file (default research/ledger.db, gitignored), same pattern as
app/sync/outbox.py. Step 3 writes only `protocols`; the other tables exist
so later steps (vault, luck, agent API, graduation, decay) add rows rather
than schema.

Append-only rules live in the database as triggers, not in callers:
- a locked protocol's hash and YAML can never change (new version instead);
- trials and holdout_evals can never be deleted (§4: every trial counts).
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_LEDGER_PATH = Path("research/ledger.db")

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

CREATE TABLE families (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('user','paper','book','video','derived')),
    source_ref  TEXT NOT NULL CHECK (length(trim(source_ref)) > 0),
    hypothesis  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    protocol_id TEXT NOT NULL REFERENCES protocols(id)
);

CREATE TABLE variants (
    id            TEXT PRIMARY KEY,
    family_id     TEXT NOT NULL REFERENCES families(id),
    strategy_hash TEXT NOT NULL UNIQUE,
    params_json   TEXT NOT NULL,
    engine_commit TEXT NOT NULL,
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

CREATE TABLE holdout_evals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_hash TEXT NOT NULL,
    protocol_id   TEXT NOT NULL REFERENCES protocols(id),
    requested_by  TEXT NOT NULL,
    approved_by   TEXT,
    ts            TEXT NOT NULL,
    metrics_json  TEXT,
    passed        INTEGER,
    UNIQUE (strategy_hash, protocol_id)
);
CREATE TRIGGER holdout_evals_no_delete BEFORE DELETE ON holdout_evals
BEGIN SELECT RAISE(ABORT, 'holdout evaluations are never deleted'); END;

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


class Ledger:
    SCHEMA_VERSION = 1

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
