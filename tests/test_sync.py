"""
Outbox + Sender tests.

Scope:
  - Outbox: enqueue, claim_batch, mark_sent/mark_failed, prune, persistence
  - Sender: happy path, partial confirms, network failures, backoff, signing
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from pathlib import Path

import httpx
import pytest

from app.sync.outbox import Outbox, OutboxRow
from app.sync.sender import Sender, SenderConfig


# =====================================================================
# Outbox
# =====================================================================

class TestOutbox:

    def test_enqueue_and_claim(self, tmp_path: Path):
        ob = Outbox(tmp_path / "test.db")
        rid = ob.enqueue("signal", {"side": "long"})
        assert rid > 0

        rows = ob.claim_batch()
        assert len(rows) == 1
        assert rows[0].kind == "signal"
        assert rows[0].payload == {"side": "long"}
        ob.close()

    def test_mark_sent_excludes_from_future_batches(self, tmp_path: Path):
        ob = Outbox(tmp_path / "test.db")
        for i in range(3):
            ob.enqueue("fill", {"i": i})

        first = ob.claim_batch()
        assert len(first) == 3
        ob.mark_sent([r.id for r in first[:2]])

        second = ob.claim_batch()
        assert len(second) == 1
        assert second[0].payload == {"i": 2}
        ob.close()

    def test_mark_failed_increments_attempts(self, tmp_path: Path):
        ob = Outbox(tmp_path / "test.db")
        ob.enqueue("signal", {"x": 1})

        rows = ob.claim_batch()
        ob.mark_failed([rows[0].id], "timeout")

        rows2 = ob.claim_batch()
        assert len(rows2) == 1
        assert rows2[0].attempts == 1
        assert rows2[0].last_error == "timeout"
        ob.close()

    def test_max_attempts_drops_from_batch(self, tmp_path: Path):
        """A row over max_attempts is poisoned — not retried."""
        ob = Outbox(tmp_path / "test.db")
        ob.enqueue("signal", {"x": 1})
        rows = ob.claim_batch()

        # Fail it 10 times.
        for _ in range(10):
            ob.mark_failed([rows[0].id], "broken")

        # Next claim should NOT include it (max_attempts default = 10).
        next_batch = ob.claim_batch(max_attempts=10)
        assert next_batch == []

        # But stats should report it as poisoned.
        s = ob.stats()
        assert s["poisoned"] == 1
        assert s["pending"] == 0
        ob.close()

    def test_persistence_across_close_and_reopen(self, tmp_path: Path):
        """Process crash → restart → outbox still has the events."""
        db = tmp_path / "test.db"
        ob = Outbox(db)
        ob.enqueue("signal", {"x": 1})
        ob.enqueue("fill", {"y": 2})
        ob.close()

        ob2 = Outbox(db)
        rows = ob2.claim_batch()
        assert len(rows) == 2
        ob2.close()

    def test_prune_removes_old_sent_rows(self, tmp_path: Path):
        ob = Outbox(tmp_path / "test.db")
        ob.enqueue("signal", {"x": 1})
        rows = ob.claim_batch()
        ob.mark_sent([rows[0].id])

        import time
        time.sleep(0.01)  # give SQLite's timestamp a moment to be in the past

        # Prune everything sent older than 0 seconds → should remove it.
        deleted = ob.prune_sent_older_than(0)
        assert deleted == 1
        ob.close()

    def test_enqueue_never_raises(self, tmp_path: Path):
        """
        Trading hot path: outbox.enqueue must not throw, ever.
        Even if we pass garbage, log and continue.
        """
        ob = Outbox(tmp_path / "test.db")
        # Non-JSON-serializable object → enqueue should swallow.
        class Weird:
            pass
        rid = ob.enqueue("signal", {"weird": Weird()})
        # default=str makes Weird stringify, so enqueue should actually
        # succeed. Verify rid > 0.
        assert rid > 0
        ob.close()

    def test_stats_distinguishes_states(self, tmp_path: Path):
        ob = Outbox(tmp_path / "test.db")
        # 3 pending, 1 sent, 1 poisoned.
        ids = [ob.enqueue("signal", {"i": i}) for i in range(5)]
        rows = ob.claim_batch()

        ob.mark_sent([rows[0].id])
        for _ in range(10):
            ob.mark_failed([rows[1].id], "broken")
        # rows[2..4] still pending.

        s = ob.stats()
        assert s["sent"] == 1
        assert s["poisoned"] == 1
        assert s["pending"] == 3
        assert s["total"] == 5
        ob.close()


# =====================================================================
# Sender — with a mock DO endpoint
# =====================================================================

class MockServer:
    """
    Mock DO endpoint. Records what was received, optionally fails on
    a configurable response code, validates HMAC signatures.
    """

    def __init__(self, hmac_secret: str = "test-secret") -> None:
        self.hmac_secret = hmac_secret
        self.received_batches: list[dict] = []
        self.fail_next_n = 0
        self.partial_confirm_count: int | None = None  # None = confirm all

    async def handler(self, request: httpx.Request) -> httpx.Response:
        body_bytes = request.content
        timestamp = request.headers.get("X-Bot-Timestamp", "")
        signature = request.headers.get("X-Bot-Signature", "")

        # Verify HMAC.
        expected = hmac.new(
            self.hmac_secret.encode(),
            timestamp.encode() + b"\n" + body_bytes,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return httpx.Response(401, json={"error": "bad signature"})

        # Optional forced failure.
        if self.fail_next_n > 0:
            self.fail_next_n -= 1
            return httpx.Response(503, json={"error": "simulated down"})

        body = json.loads(body_bytes)
        self.received_batches.append(body)

        ids = [e["id"] for e in body["events"]]
        if self.partial_confirm_count is not None:
            ids = ids[:self.partial_confirm_count]
        return httpx.Response(200, json={"received": ids})


@pytest.fixture
def mock_server():
    return MockServer()


@pytest.fixture
def patched_httpx(monkeypatch, mock_server):
    """
    Replace AsyncClient with one that uses a MockTransport pointed at
    our mock server. The Sender code is unchanged.
    """
    from httpx import AsyncClient, MockTransport

    original_init = AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = MockTransport(mock_server.handler)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(AsyncClient, "__init__", patched_init)
    return mock_server


def make_config(**overrides) -> SenderConfig:
    defaults = dict(
        endpoint_url="https://mock.local/api/bot/events",
        hmac_secret="test-secret",
        bot_id="test-bot",
        poll_interval_seconds=0.05,
        batch_size=10,
        max_attempts=10,
        request_timeout_seconds=2.0,
        prune_after_seconds=3600,
        prune_every_seconds=10.0,
    )
    defaults.update(overrides)
    return SenderConfig(**defaults)


class TestSender:

    async def test_happy_path_sends_and_marks_sent(
        self, tmp_path: Path, patched_httpx
    ):
        ob = Outbox(tmp_path / "test.db")
        ob.enqueue("signal", {"x": 1})
        ob.enqueue("fill", {"y": 2})

        sender = Sender(ob, make_config())
        await sender.start()
        # Give the loop a few ticks.
        await asyncio.sleep(0.2)
        await sender.stop()

        # Outbox should be empty of pending.
        assert ob.claim_batch() == []
        # Server saw both events.
        assert len(patched_httpx.received_batches) >= 1
        all_events = [
            e for batch in patched_httpx.received_batches
            for e in batch["events"]
        ]
        assert len(all_events) == 2
        ob.close()

    async def test_signature_validates(self, tmp_path: Path, patched_httpx):
        """If the secret matches, server accepts. Test by checking that
        events actually flow through (a wrong signature → 401 →
        events stay pending)."""
        ob = Outbox(tmp_path / "test.db")
        ob.enqueue("signal", {"x": 1})

        sender = Sender(ob, make_config())
        await sender.start()
        await asyncio.sleep(0.15)
        await sender.stop()

        assert ob.claim_batch() == []  # all sent
        ob.close()

    async def test_wrong_secret_keeps_events_pending(
        self, tmp_path: Path, patched_httpx
    ):
        ob = Outbox(tmp_path / "test.db")
        ob.enqueue("signal", {"x": 1})

        # Sender uses a different secret than the server expects.
        sender = Sender(ob, make_config(hmac_secret="WRONG"))
        await sender.start()
        await asyncio.sleep(0.15)
        await sender.stop()

        # 401 → mark_failed → still pending (with attempts > 0).
        rows = ob.claim_batch()
        assert len(rows) == 1
        assert rows[0].attempts > 0
        ob.close()

    async def test_server_down_then_recovers(
        self, tmp_path: Path, patched_httpx
    ):
        ob = Outbox(tmp_path / "test.db")
        ob.enqueue("signal", {"x": 1})

        # Fail the first 2 attempts, succeed after.
        patched_httpx.fail_next_n = 2

        sender = Sender(ob, make_config())
        await sender.start()
        # Backoff makes this slow — give it time.
        await asyncio.sleep(2.0)
        await sender.stop()

        assert ob.claim_batch() == []  # eventually sent
        ob.close()

    async def test_partial_confirm(self, tmp_path: Path, patched_httpx):
        """
        Server confirms only first 1 of 3 events in a single batch.
        After ONE tick, 1 should be sent, 2 should be pending with
        attempts incremented. The full drain is covered by happy_path;
        this test isolates the partial-confirm bookkeeping.
        """
        ob = Outbox(tmp_path / "test.db")
        ob.enqueue("signal", {"x": 1})
        ob.enqueue("fill", {"y": 2})
        ob.enqueue("reconcile", {"z": 3})

        patched_httpx.partial_confirm_count = 1

        sender = Sender(ob, make_config())
        # Don't start the loop — call _tick directly so we get exactly
        # one batch of network behavior.
        # _tick uses self._client, which start()/stop() manages — so
        # do the minimal setup manually.
        from httpx import AsyncClient
        sender._client = AsyncClient(timeout=2.0)
        try:
            await sender._tick()
        finally:
            await sender._client.aclose()

        rows = ob.claim_batch()
        assert len(rows) == 2
        assert all(r.attempts > 0 for r in rows)
        ob.close()


# =====================================================================
# Backoff math
# =====================================================================

def test_backoff_caps_at_60_seconds(tmp_path: Path):
    ob = Outbox(tmp_path / "test.db")
    sender = Sender(ob, SenderConfig(
        endpoint_url="x",
        hmac_secret="x",
        poll_interval_seconds=2.0,
    ))
    # No failures → poll interval.
    sender._consecutive_failures = 0
    assert sender._next_sleep_interval() == 2.0
    # Many failures → capped at 60.
    sender._consecutive_failures = 100
    assert sender._next_sleep_interval() == 60.0
    ob.close()
