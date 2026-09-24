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

    Additive column migrations are the one thing ledger.sql's own
    `CREATE ... IF NOT EXISTS` convention can't express — SQLite has no
    `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. Those are listed in
    `_COLUMN_MIGRATIONS` below and applied here, defensively (a database
    created fresh from this same ledger.sql already has the column via its
    CREATE TABLE statement, so "duplicate column" is the expected, ignored
    outcome there).
    """
    sql = LEDGER_SQL_PATH.read_text()
    conn.executescript(sql)
    for table, column, coltype in _COLUMN_MIGRATIONS:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc):
                raise
    conn.commit()


# (table, column, SQL type) — additive migrations for a ledger.db created
# before that column existed in ledger.sql's CREATE TABLE. Each entry here
# must also appear in ledger.sql's CREATE TABLE so a FRESH database and a
# migrated one converge to the same schema.
_COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("hypotheses", "sharpe_oos_per_trade", "REAL"),
)


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


def get_readonly_connection(db_path: Path | str = LEDGER_DB_PATH) -> sqlite3.Connection:
    """A connection that cannot write, enforced by SQLite itself
    (`PRAGMA query_only = ON`) — not by filtering what SQL text a caller
    sends. Every INSERT/UPDATE/DELETE/CREATE/DROP/ALTER raises
    `sqlite3.OperationalError: attempt to write a readonly database`
    regardless of how the statement was constructed; there is no string
    the caller (the phase-7 ledger chat agent, or anything else) can send
    through this connection that bypasses it, short of a wholly separate
    connection object.

    For research.ledger_agent (docs/research-loop/PHASE-PROMPTS.md phase
    7): "read-only database credentials, enforced at the connection level
    not by prompt." Never runs `apply_schema` — a read-only connection
    must not attempt the CREATE TABLE/ALTER TABLE calls that involves, and
    a chat agent has nothing useful to say about a ledger that doesn't
    exist yet. Raises `sqlite3.OperationalError` if `db_path` doesn't
    exist, rather than silently creating it (get_connection's job, not
    this one's).
    """
    db_path = Path(db_path)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn
