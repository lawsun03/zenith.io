"""news_straddle LIVE wall-clock scheduler (B92).

Arms a resting OCO stop-entry straddle a fixed lead before each scheduled news
release. The bar-driven NewsStraddleDetector (B89) cannot do this live: a resting
exchange stop must be placed BEFORE the print so it is working when 08:30 fires.
This scheduler buffers 1-min bars, locks the pre-release range, and places the OCO
via the broker's place_oco_stop_entries — the proven bracket-after-fill path then
attaches the tight stop + tp_r target when a leg fills.

WINDOW NOTE (honesty / Rule 12): the B85/B89 oracle locked the range over
[release-15min, release). To be working before the print, this scheduler locks the
range over [arm_time - range_minutes, arm_time) where arm_time = release -
arm_lead_seconds — i.e. the same-length window shifted earlier by the lead. The
straddle MECHANISM (breakout of the pre-news consolidation) is unchanged; exact
P&L re-validation against the shifted window is a documented follow-up.

Default-off: only constructed/started by main.py when
strategy.news_straddle_live_enabled is True. Never auto-enables anything.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable

from app.broker.events import Bar

log = logging.getLogger(__name__)

_PENDING = "pending"
_ARMED = "armed"
_SKIPPED = "skipped"


@dataclass
class _SchedEvent:
    ts: datetime                      # release timestamp, UTC-aware
    status: str = _PENDING
    buy_id: str | None = None
    sell_id: str | None = None
    rhigh: Decimal | None = None
    rlow: Decimal | None = None


class NewsStraddleScheduler:
    """Background task: arm one resting OCO straddle per scheduled event."""

    def __init__(
        self,
        broker,
        *,
        instrument: str,
        event_times: list[datetime],
        offset_ticks: int,
        tp_r: Decimal,
        tick: Decimal,
        size: int,
        arm_lead_seconds: int = 120,
        range_minutes: int = 15,
        min_range_bars: int = 5,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.broker = broker
        self.instrument = instrument
        self.offset = Decimal(offset_ticks) * tick
        self.tp_r = tp_r
        self.size = size
        self.arm_lead_seconds = arm_lead_seconds
        self.range_minutes = range_minutes
        self.min_range_bars = min_range_bars
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._events = [_SchedEvent(ts=t) for t in sorted(event_times)]
        # Rolling (ts, high, low) buffer; trimmed to ~2× the range window.
        self._buffer: list[tuple[datetime, Decimal, Decimal]] = []
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    # ---- bar intake (registered via broker.on_bar) ----
    async def on_bar(self, bar: Bar) -> None:
        if bar.instrument and bar.instrument != self.instrument:
            return
        self._buffer.append((bar.ts, bar.high, bar.low))
        cutoff = bar.ts - timedelta(minutes=self.range_minutes * 2)
        if self._buffer[0][0] < cutoff:
            self._buffer = [b for b in self._buffer if b[0] >= cutoff]

    def _compute_range(self, arm_ts: datetime) -> "tuple[Decimal, Decimal] | None":
        start = arm_ts - timedelta(minutes=self.range_minutes)
        bars = [b for b in self._buffer if start <= b[0] < arm_ts]
        if len(bars) < self.min_range_bars:
            return None
        return max(b[1] for b in bars), min(b[2] for b in bars)

    async def _arm_event(self, ev: _SchedEvent) -> None:
        if ev.status != _PENDING:
            return  # one straddle per event — never re-arm
        arm_ts = ev.ts - timedelta(seconds=self.arm_lead_seconds)
        rng = self._compute_range(arm_ts)
        if rng is None:
            ev.status = _SKIPPED
            log.warning("news_straddle scheduler: %s skipped (< %d pre-range bars in buffer)",
                        ev.ts, self.min_range_bars)
            return
        high, low = rng
        ev.rhigh, ev.rlow = high, low
        buy_stop = high + self.offset
        sell_stop = low - self.offset
        log.info("news_straddle scheduler arming %s: range=[%s-%s] buy=%s sell=%s R=%s",
                 ev.ts, low, high, buy_stop, sell_stop, self.offset)
        try:
            buy_id, sell_id = await self.broker.place_oco_stop_entries(
                self.instrument, buy_stop, sell_stop,
                stop_r=self.offset, tp_r=self.tp_r, size=self.size,
            )
        except Exception:
            ev.status = _SKIPPED
            log.exception("news_straddle scheduler: place_oco_stop_entries failed for %s", ev.ts)
            return
        if not (buy_id and sell_id):
            ev.status = _SKIPPED
            log.error("news_straddle scheduler: OCO not placed for %s", ev.ts)
            return
        ev.buy_id, ev.sell_id, ev.status = buy_id, sell_id, _ARMED

    # ---- lifecycle ----
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

    def _next_pending(self) -> "_SchedEvent | None":
        now = self._now()
        for ev in self._events:
            if ev.status == _PENDING and ev.ts > now:
                return ev
        return None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            ev = self._next_pending()
            if ev is None:
                # No upcoming events — idle, re-checking hourly (events file may
                # be reloaded, and we avoid a tight spin).
                sleep_s = 3600.0
            else:
                arm_ts = ev.ts - timedelta(seconds=self.arm_lead_seconds)
                sleep_s = max(0.0, (arm_ts - self._now()).total_seconds())
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_s)
                break  # stop requested
            except asyncio.TimeoutError:
                pass
            if self._stop_event.is_set():
                break
            if ev is not None and ev.status == _PENDING:
                try:
                    await self._arm_event(ev)
                except Exception:
                    log.exception("news_straddle scheduler: arm failed for %s", ev.ts)

    def state(self) -> dict:
        """Live dashboard view (Rule 13). Pure read."""
        return {
            "instrument": self.instrument,
            "offset": str(self.offset),
            "tp_r": str(self.tp_r),
            "size": self.size,
            "events": [
                {
                    "ts": ev.ts.isoformat(),
                    "status": ev.status,
                    "range_high": str(ev.rhigh) if ev.rhigh is not None else None,
                    "range_low": str(ev.rlow) if ev.rlow is not None else None,
                }
                for ev in self._events
            ],
        }
