"""
Outbox — durable local queue for outbound events.

Why SQLite:
  - Single-file, no separate service to run
  - Atomic writes survive process crashes
  - Built into Python's stdlib
  - WAL mode handles concurrent reader/writer cleanly

Schema (bumped via PRAGMA user_version on migrations):
  CREATE TABLE outbox (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at   TEXT NOT NULL,         -- ISO8601 UTC
      kind         TEXT NOT NULL,         -- 'signal' | 'fill' | 'reconcile' | 'status'
      payload      TEXT NOT NULL,         -- JSON
      attempts     INTEGER NOT NULL DEFAULT 0,
      last_attempt TEXT,                  -- ISO8601 UTC; NULL = never tried
      last_error   TEXT
  )
  CREATE INDEX idx_outbox_pending ON outbox(id) WHERE last_attempt IS NULL;

Operations:
  - enqueue(kind, payload)            — bot writes here
  - claim_batch(limit, max_attempts)  — worker reads pending events
  - mark_sent(ids)                    — worker confirms successful POST
  - mark_failed(ids, error)           — worker records a failure for retry
  - prune_sent_older_than(seconds)    — periodic cleanup

The worker (sender.py) is async-driven; the outbox itself is sync
because SQLite is sync. We dispatch sync calls into a thread pool
when called from async code, but in practice the operations are
microseconds and direct calls work fine on a local file.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboxRow:
    id: int
    created_at: datetime
    kind: str
    payload: dict
    attempts: int
    last_error: str | None


class Outbox:
    """
    SQLite-backed durable queue. Thread-safe via a lock; SQLite itself
    serializes writes anyway, but the lock prevents Python-level races
    on the connection.
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        # Connection per-instance; check_same_thread=False because the
        # async worker may be on a different thread than tests/dashboard.
        # The threading lock makes this safe.
        self._conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we handle txn boundaries
        )
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        """Create tables if missing; migrate if version is older."""
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")

            current = self._conn.execute(
                "PRAGMA user_version"
            ).fetchone()[0]

            if current == 0:
                self._conn.executescript("""
                    CREATE TABLE IF NOT EXISTS outbox (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        last_attempt TEXT,
                        last_error TEXT,
                        sent_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_outbox_pending
                        ON outbox(id) WHERE sent_at IS NULL;
                    CREATE INDEX IF NOT EXISTS idx_outbox_sent_at
                        ON outbox(sent_at) WHERE sent_at IS NOT NULL;
                """)
                self._conn.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")
            elif current < self.SCHEMA_VERSION:
                # Future migrations land here.
                raise RuntimeError(
                    f"Outbox at {self.path} is version {current}; "
                    f"expected {self.SCHEMA_VERSION}. Migration not implemented."
                )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, kind: str, payload: dict) -> int:
        """
        Write an event to the queue. Returns the assigned id.

        Called from event handlers — this is on the trading hot path,
        so it must be fast and never raise. We catch and log rather
        than propagate.
        """
        try:
            now = datetime.now(timezone.utc).isoformat()
            payload_json = json.dumps(payload, default=str)
            with self._lock:
                cur = self._conn.execute(
                    "INSERT INTO outbox (created_at, kind, payload) "
                    "VALUES (?, ?, ?)",
                    (now, kind, payload_json),
                )
                return cur.lastrowid or 0
        except Exception as e:
            # Hard failure — DB unavailable, disk full, etc. Log and
            # drop. The bot must never crash because the outbox is sad.
            log.exception("Outbox.enqueue failed (kind=%s): %s", kind, e)
            return 0

    def claim_batch(
        self,
        limit: int = 50,
        max_attempts: int = 10,
    ) -> list[OutboxRow]:
        """
        Get up to `limit` pending rows for sending.

        Skips rows that have already been sent (sent_at IS NOT NULL)
        or that have exceeded max_attempts (poison messages).

        Doesn't lock or claim — multiple workers would race, but we
        only run one worker. If we ever go multi-worker, add a
        `claimed_at` column and SELECT ... FOR UPDATE-like semantics.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created_at, kind, payload, attempts, last_error "
                "FROM outbox "
                "WHERE sent_at IS NULL AND attempts < ? "
                "ORDER BY id "
                "LIMIT ?",
                (max_attempts, limit),
            ).fetchall()

        return [
            OutboxRow(
                id=r["id"],
                created_at=datetime.fromisoformat(r["created_at"]),
                kind=r["kind"],
                payload=json.loads(r["payload"]),
                attempts=r["attempts"],
                last_error=r["last_error"],
            )
            for r in rows
        ]

    def mark_sent(self, ids: list[int]) -> None:
        if not ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            placeholders = ",".join("?" * len(ids))
            self._conn.execute(
                f"UPDATE outbox SET sent_at = ? WHERE id IN ({placeholders})",
                (now, *ids),
            )

    def mark_failed(self, ids: list[int], error: str) -> None:
        if not ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        # Truncate error to keep the column reasonable.
        err = error[:500]
        with self._lock:
            placeholders = ",".join("?" * len(ids))
            self._conn.execute(
                f"UPDATE outbox SET attempts = attempts + 1, "
                f"last_attempt = ?, last_error = ? "
                f"WHERE id IN ({placeholders})",
                (now, err, *ids),
            )

    def prune_sent_older_than(self, seconds: int) -> int:
        """
        Delete sent rows older than `seconds`. Keeps the DB from
        growing forever. Returns rows deleted.

        Caller should run this periodically (every hour or so) from
        the same worker that drains the outbox.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=seconds)
        ).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM outbox WHERE sent_at IS NOT NULL AND sent_at < ?",
                (cutoff,),
            )
            return cur.rowcount

    # ------------------------------------------------------------------
    # Stats — for the dashboard
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        """Pending, failed (over max attempts), sent, total."""
        with self._lock:
            row = self._conn.execute("""
                SELECT
                    SUM(CASE WHEN sent_at IS NULL AND attempts < 10 THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN sent_at IS NULL AND attempts >= 10 THEN 1 ELSE 0 END) AS poisoned,
                    SUM(CASE WHEN sent_at IS NOT NULL THEN 1 ELSE 0 END) AS sent,
                    COUNT(*) AS total
                FROM outbox
            """).fetchone()
        return {
            "pending":   row["pending"] or 0,
            "poisoned":  row["poisoned"] or 0,
            "sent":      row["sent"] or 0,
            "total":     row["total"] or 0,
        }
