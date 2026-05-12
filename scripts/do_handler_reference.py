"""
Reference handler for the DigitalOcean side.

Drop this into your existing FastAPI app to receive bot events. Adjust
the storage layer (here SQLite) to match your existing journal DB.

Endpoint contract (matches Sender):
  POST /api/bot/events
  Headers:
    Content-Type: application/json
    X-Bot-Signature: hmac_sha256(secret, "{timestamp}\\n{body}")
    X-Bot-Timestamp: unix epoch seconds
  Body:
    {
      "bot_id": "lawrence-mgc",
      "events": [
        {"id": 42, "kind": "fill", "payload": {...}, "created_at": "..."},
        ...
      ]
    }
  Response:
    200 OK with {"received": [42, 43]}  - ids that were stored
    401  - HMAC validation failed
    400  - body malformed

Idempotency: PRIMARY KEY (bot_id, event_id) — same bot retrying the
same event id is a no-op insert.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request

# === Configuration ===
# Replace with however you store secrets (env var, vault, etc.).
BOT_HMAC_SECRET = "REPLACE_WITH_SHARED_SECRET"

# Acceptable clock skew between bot and DO server. 5 minutes is loose
# enough for laptop sleep/wake; tighter is better if your hosts are
# both NTP-synced.
TIMESTAMP_TOLERANCE_SECONDS = 300

# Where the durable journal lives on DO.
DB_PATH = Path("/var/lib/topstep-bot-journal.db")


# ---------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------

def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bot_events (
            bot_id      TEXT NOT NULL,
            event_id    INTEGER NOT NULL,
            kind        TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            received_at TEXT NOT NULL,
            payload     TEXT NOT NULL,
            PRIMARY KEY (bot_id, event_id)
        );
        CREATE INDEX IF NOT EXISTS idx_bot_events_kind
            ON bot_events(bot_id, kind, created_at);
    """)


@contextmanager
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        _init_schema(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------

def _verify_signature(body: bytes, timestamp: str, signature: str) -> bool:
    expected = hmac.new(
        BOT_HMAC_SECRET.encode(),
        timestamp.encode() + b"\n" + body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _verify_timestamp(timestamp_str: str) -> bool:
    try:
        ts = int(timestamp_str)
    except ValueError:
        return False
    delta = abs(time.time() - ts)
    return delta <= TIMESTAMP_TOLERANCE_SECONDS


# ---------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------

router = APIRouter()


@router.post("/api/bot/events")
async def receive_events(
    request: Request,
    x_bot_signature: str = Header(...),
    x_bot_timestamp: str = Header(...),
):
    body = await request.body()

    if not _verify_timestamp(x_bot_timestamp):
        raise HTTPException(status_code=401, detail="timestamp out of range")

    if not _verify_signature(body, x_bot_timestamp, x_bot_signature):
        raise HTTPException(status_code=401, detail="bad signature")

    try:
        data = json.loads(body)
        bot_id = str(data["bot_id"])
        events = data["events"]
        assert isinstance(events, list)
    except (json.JSONDecodeError, KeyError, AssertionError) as e:
        raise HTTPException(status_code=400, detail=f"malformed: {e}")

    received_ids: list[int] = []
    received_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    with get_conn() as conn:
        for event in events:
            try:
                event_id = int(event["id"])
                kind = str(event["kind"])
                created_at = str(event["created_at"])
                payload = event["payload"]
            except (KeyError, ValueError, TypeError):
                # Bad event in batch — skip it. The bot will retry until
                # it hits max_attempts and then poison-pill on its side.
                continue

            try:
                conn.execute(
                    "INSERT OR IGNORE INTO bot_events "
                    "(bot_id, event_id, kind, created_at, received_at, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        bot_id, event_id, kind,
                        created_at, received_at,
                        json.dumps(payload, default=str),
                    ),
                )
                received_ids.append(event_id)
            except sqlite3.Error:
                # Single-event failure shouldn't tank the batch. Log
                # server-side and skip; bot will retry this id.
                pass

    return {"received": received_ids}
