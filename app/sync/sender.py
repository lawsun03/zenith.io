"""
Sender — async worker that ships outbox events to DO.

Lifecycle mirrors the reconciler:

    sender = Sender(outbox, endpoint_url, hmac_secret)
    await sender.start()
    # ... bot runs ...
    await sender.stop()

The wire protocol is dead simple — POST a JSON body to one endpoint
on your DO FastAPI:

    POST /api/bot/events
    Headers:
      Content-Type: application/json
      X-Bot-Signature: hmac_sha256(secret, body)
      X-Bot-Timestamp: <unix epoch seconds>
    Body:
      {"events": [{"id": 42, "kind": "fill", "payload": {...}, ...}, ...]}

    Response:
      200 OK with {"received": [42, 43, ...]} = ids confirmed
      4xx/5xx = retry with backoff

The HMAC + timestamp prevents replay attacks if anyone sniffs the
endpoint. Your DO side validates: signature matches, timestamp is
within ±5 minutes of server time. Reject otherwise.

Design properties:
  - Bot continues trading if DO is down. Outbox accumulates locally.
  - Retries with exponential backoff capped at 60s. Doesn't hammer.
  - Poison-message handling: rows with too many attempts stay in DB
    but stop being sent; dashboard surfaces them.
  - Network errors and HTTP errors are treated identically — both
    increment attempts.
  - Idempotency: events carry their outbox.id; DO side deduplicates
    by (bot_id, event_id) primary key.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from .outbox import Outbox, OutboxRow

log = logging.getLogger(__name__)


@dataclass
class SenderConfig:
    """Tunables for the worker loop."""

    endpoint_url: str
    hmac_secret: str
    bot_id: str = "default"            # for multi-bot setups; included in payload
    poll_interval_seconds: float = 2.0
    batch_size: int = 50
    max_attempts: int = 10
    request_timeout_seconds: float = 10.0
    prune_after_seconds: int = 7 * 24 * 3600  # 1 week of sent rows kept for audit
    prune_every_seconds: float = 3600.0


class Sender:
    """Outbox → HTTPS POST worker."""

    def __init__(self, outbox: Outbox, config: SenderConfig) -> None:
        self.outbox = outbox
        self.config = config

        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self._client: Optional[httpx.AsyncClient] = None

        # Backoff state — escalates on consecutive batch failures.
        self._consecutive_failures = 0
        self._last_prune_ts: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        self._client = httpx.AsyncClient(
            timeout=self.config.request_timeout_seconds,
        )
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())
        log.info("Sender started: endpoint=%s", self.config.endpoint_url)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except asyncio.TimeoutError:
            log.warning("Sender stop timed out; cancelling")
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        log.info("Sender stopped")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    await self._tick()
                except Exception:
                    log.exception("Sender tick failed")
                    self._consecutive_failures += 1

                # Periodic prune.
                now = time.monotonic()
                if now - self._last_prune_ts >= self.config.prune_every_seconds:
                    self._last_prune_ts = now
                    try:
                        deleted = self.outbox.prune_sent_older_than(
                            self.config.prune_after_seconds
                        )
                        if deleted:
                            log.info("Pruned %d sent rows from outbox", deleted)
                    except Exception:
                        log.exception("Prune failed")

                # Backoff-aware sleep.
                interval = self._next_sleep_interval()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=interval,
                    )
                    break  # stop fired
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            log.info("Sender loop cancelled")
            raise

    def _next_sleep_interval(self) -> float:
        """Exponential backoff capped at 60s."""
        if self._consecutive_failures == 0:
            return self.config.poll_interval_seconds
        # 2, 4, 8, 16, 32, 60, 60, ...
        return min(60.0, self.config.poll_interval_seconds * (2 ** min(self._consecutive_failures, 5)))

    # ------------------------------------------------------------------
    # One tick
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        """Try to send one batch. Update outbox state based on result."""
        batch = self.outbox.claim_batch(
            limit=self.config.batch_size,
            max_attempts=self.config.max_attempts,
        )
        if not batch:
            self._consecutive_failures = 0
            return

        try:
            confirmed_ids = await self._post_batch(batch)
        except Exception as e:
            # Network error, timeout, etc. Mark whole batch failed.
            self.outbox.mark_failed([r.id for r in batch], str(e))
            self._consecutive_failures += 1
            log.warning(
                "Sender batch failed (size=%d, consecutive=%d): %s",
                len(batch), self._consecutive_failures, e,
            )
            return

        # Server acked some subset of ids. The rest are failures.
        confirmed_set = set(confirmed_ids)
        success_ids = [r.id for r in batch if r.id in confirmed_set]
        failed_ids = [r.id for r in batch if r.id not in confirmed_set]

        if success_ids:
            self.outbox.mark_sent(success_ids)
        if failed_ids:
            self.outbox.mark_failed(
                failed_ids,
                "server did not include id in response",
            )
            log.warning(
                "Sender: server confirmed %d/%d events",
                len(success_ids), len(batch),
            )

        # Reset failure counter only if at least one event made it.
        if success_ids:
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1

    async def _post_batch(self, batch: list[OutboxRow]) -> list[int]:
        """Send the batch and return the list of ids the server acked."""
        assert self._client is not None
        body = {
            "bot_id": self.config.bot_id,
            "events": [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "payload": r.payload,
                    "created_at": r.created_at.isoformat(),
                }
                for r in batch
            ],
        }
        body_bytes = json.dumps(body, default=str).encode("utf-8")

        timestamp = str(int(time.time()))
        signature = self._sign(body_bytes, timestamp)

        headers = {
            "Content-Type": "application/json",
            "X-Bot-Signature": signature,
            "X-Bot-Timestamp": timestamp,
        }

        response = await self._client.post(
            self.config.endpoint_url,
            content=body_bytes,
            headers=headers,
        )
        response.raise_for_status()

        data = response.json()
        return list(data.get("received", []))

    def _sign(self, body: bytes, timestamp: str) -> str:
        """HMAC-SHA256 of timestamp + body. Server validates the same."""
        msg = timestamp.encode("utf-8") + b"\n" + body
        return hmac.new(
            self.config.hmac_secret.encode("utf-8"),
            msg,
            hashlib.sha256,
        ).hexdigest()
