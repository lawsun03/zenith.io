"""
Hourly health-check email.

Installs a TailHandler on the root logger to capture the last N records
in memory, then fires once per hour (at :00) to email a status snapshot.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from app.notifications.email import EmailNotifier
    from app.risk.state import RiskState

CT = ZoneInfo("America/Chicago")
log = logging.getLogger(__name__)


class TailHandler(logging.Handler):
    """Logging handler that keeps the last N formatted records in a deque."""

    def __init__(self, capacity: int = 50) -> None:
        super().__init__()
        self._records: deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._records.append(self.format(record))
        except Exception:
            self.handleError(record)

    def tail(self, n: int = 10) -> list[str]:
        return list(self._records)[-n:]


class HourlyHealthScheduler:
    """Fires once per hour at :00, emails the last N log lines + risk state."""

    def __init__(
        self,
        notifier: "EmailNotifier",
        risk_state: "RiskState",
        tail_handler: TailHandler,
        n_lines: int = 50,
    ) -> None:
        self.notifier = notifier
        self.risk_state = risk_state
        self.tail_handler = tail_handler
        self.n_lines = n_lines
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=3.0)
        except asyncio.TimeoutError:
            self._task.cancel()
        self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            sleep_secs = self._seconds_until_next_hour()
            log.info("Health scheduler: next check in %.0fs", sleep_secs)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_secs)
                break
            except asyncio.TimeoutError:
                pass
            if self._stop_event.is_set():
                break
            try:
                await self._send_health()
            except Exception:
                log.exception("Hourly health check failed")

    def _seconds_until_next_hour(self) -> float:
        now = datetime.now(CT)
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        return (next_hour - now).total_seconds()

    async def _send_health(self) -> None:
        if not self.notifier.enabled:
            return

        now_ct = datetime.now(CT).strftime("%Y-%m-%d %H:%M CT")
        lockout = self.risk_state.locked_out
        status = (
            f"LOCKED OUT — {lockout.code}: {lockout.message}"
            if lockout else "OK"
        )

        lines = self.tail_handler.tail(self.n_lines)
        log_block = "\n".join(lines) if lines else "(no log records yet)"

        body = (
            f"Hourly health check: {now_ct}\n\n"
            f"STATUS:          {status}\n"
            f"Open contracts:  {self.risk_state.open_contracts}\n"
            f"Daily P&L:       ${self.risk_state.daily_pnl}\n"
            f"Equity:          ${self.risk_state.current_equity}\n"
            f"High-water:      ${self.risk_state.equity_high_water}\n\n"
            f"Last {self.n_lines} log lines:\n"
            f"{'-' * 60}\n"
            f"{log_block}"
        )

        await self.notifier.send(
            subject=f"[health] {now_ct} — {status}",
            body=body,
        )
