"""Connection handling and schema application for the research ledger.

Applying the schema is idempotent (every statement in ledger.sql is
`CREATE ... IF NOT EXISTS`), so `get_connection` always runs it — callers
never need a separate "init" step, and there's no drift between what's on
disk and what the DDL file says the ledger should look like.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from research.ledger.paths import LEDGER_DB_PATH, LEDGER_SQL_PATH


def apply_schema(conn: sqlite3.Connection) -> None:
    """Run docs/research-loop/ledger.sql against `conn`.

    That file is the single source of truth for the schema, including the
    append-only triggers — nothing here re-derives or duplicates it.
    """
    sql = LEDGER_SQL_PATH.read_text()
    conn.executescript(sql)
    conn.commit()


def get_connection(db_path: Path | str = LEDGER_DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) the ledger database with the schema applied.

    `PRAGMA foreign_keys = ON` is per-connection in SQLite, not persisted in
    the file, so it's set here explicitly rather than relying on the copy
    inside ledger.sql (which only takes effect for the connection that ran
    the script).
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    apply_schema(conn)
    return conn
