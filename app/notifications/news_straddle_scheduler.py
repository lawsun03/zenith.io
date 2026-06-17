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
from typing import Awaitable, Callable

from app.broker.events import Bar

log = logging.getLogger(__name__)

_PENDING = "pending"
_ARMED = "armed"
_SKIPPED = "skipped"
_RECOVERED = "recovered"   # informational, not terminal


@dataclass
class _SchedEvent:
    ts: datetime
    status: str = _PENDING
    buy_id: str | None = None
    sell_id: str | None = None
    rhigh: Decimal | None = None
    rlow: Decimal | None = None
    reason: str | None = None


class NewsStraddleScheduler:
    """Background task: arm one resting OCO straddle per scheduled event."""

    def __init__(
        self,
        broker,
        *,
        instrument: str,
        event_type: str,
        event_times: list[datetime],
        offset_ticks: int,
        tp_r: Decimal,
        tick: Decimal,
        size: int,
        arm_lead_seconds: int = 120,
        range_minutes: int = 15,
        min_range_bars: int = 5,
        preflight_lead_seconds: int = 300,
        retry_interval_seconds: int = 60,
        alert_fn: "Callable[[dict], Awaitable[None]] | None" = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.broker = broker
        self.instrument = instrument
        self.event_type = event_type
        self.offset = Decimal(offset_ticks) * tick
        self.tp_r = tp_r
        self.size = size
        self.arm_lead_seconds = arm_lead_seconds
        self.range_minutes = range_minutes
        self.min_range_bars = min_range_bars
        self.preflight_lead_seconds = preflight_lead_seconds
        self.retry_interval_seconds = retry_interval_seconds
        self._alert_fn = alert_fn
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._events = [_SchedEvent(ts=t) for t in sorted(event_times)]
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def _alert(self, kind: str, ev: _SchedEvent, reason: str = "", **extra) -> None:
        ev.reason = reason or None
        if self._alert_fn is None:
            return
        payload = {
            "kind": kind,
            "event_type": self.event_type,
            "instrument": self.instrument,
            "release_ts": ev.ts.isoformat(),
            "reason": reason,
            "range_high": str(ev.rhigh) if ev.rhigh is not None else None,
            "range_low": str(ev.rlow) if ev.rlow is not None else None,
            "size": self.size,
            **extra,
        }
        try:
            await self._alert_fn(payload)
        except Exception:
            log.exception("news_straddle scheduler: alert_fn raised")

    async def _fetch_range(self, arm_ts: datetime) -> "tuple[Decimal | None, Decimal | None, int]":
        start = arm_ts - timedelta(minutes=self.range_minutes)
        bars = await self.broker.get_historical_bars(
            timeframe="1min", start_time=start, end_time=arm_ts, instrument=self.instrument,
        )
        window = [b for b in bars if start <= b.ts < arm_ts]
        if len(window) < self.min_range_bars:
            return None, None, len(window)
        return max(b.high for b in window), min(b.low for b in window), len(window)

    async def _is_ready(self) -> "tuple[bool, int, str]":
        """Pre-flight readiness on data-so-far: enough 1-min bars in the last
        range_minutes. Any fetch error (incl. broker not connected) = not ready."""
        now = self._now()
        start = now - timedelta(minutes=self.range_minutes)
        try:
            bars = await self.broker.get_historical_bars(
                timeframe="1min", start_time=start, end_time=now, instrument=self.instrument,
            )
        except Exception:
            return False, 0, "broker_unavailable"
        n = len([b for b in bars if start <= b.ts < now])
        if n < self.min_range_bars:
            return False, n, f"insufficient_bars:{n}/{self.min_range_bars}"
        return True, n, ""

    async def _arm_event(self, ev: _SchedEvent) -> None:
        if ev.status != _PENDING:
            return  # one straddle per event — never re-arm
        arm_ts = ev.ts - timedelta(seconds=self.arm_lead_seconds)
        try:
            high, low, n = await self._fetch_range(arm_ts)
        except Exception:
            ev.status = _SKIPPED
            log.exception("news_straddle scheduler: range fetch failed for %s", ev.ts)
            await self._alert("skipped", ev, reason="fetch_error")
            return
        if high is None:
            ev.status = _SKIPPED
            log.warning("news_straddle scheduler: %s skipped (%d/%d 1-min bars)",
                        ev.ts, n, self.min_range_bars)
            await self._alert("skipped", ev, reason=f"insufficient_bars:{n}/{self.min_range_bars}")
            return
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
            await self._alert("skipped", ev, reason="oco_error")
            return
        if not (buy_id and sell_id):
            ev.status = _SKIPPED
            log.error("news_straddle scheduler: OCO not placed for %s", ev.ts)
            await self._alert("skipped", ev, reason="oco_not_placed")
            return
        ev.buy_id, ev.sell_id, ev.status = buy_id, sell_id, _ARMED
        await self._alert("armed", ev, reason="")

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

    async def _preflight(self, ev: _SchedEvent) -> bool:
        """Readiness check ~preflight_lead before release. Fires an early-warning
        alert (with time to react) if at risk. Returns True if ready/on-track."""
        ok, n, reason = await self._is_ready()
        if ok:
            log.info("news_straddle scheduler preflight OK %s (%d bars)", ev.ts, n)
            return True
        log.warning("news_straddle scheduler preflight AT-RISK %s: %s", ev.ts, reason)
        await self._alert("early_warning", ev, reason=reason)
        return False

    async def _sleep_until(self, when: datetime) -> bool:
        """Sleep until `when` (interruptible by stop). Returns True if stop was set."""
        secs = max(0.0, (when - self._now()).total_seconds())
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=secs)
            return True
        except asyncio.TimeoutError:
            return False

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            ev = self._next_pending()
            if ev is None:
                if await self._sleep_until(self._now() + timedelta(hours=1)):
                    break
                continue
            preflight_ts = ev.ts - timedelta(seconds=self.preflight_lead_seconds)
            arm_ts = ev.ts - timedelta(seconds=self.arm_lead_seconds)
            if await self._sleep_until(preflight_ts):
                break
            if ev.status != _PENDING:
                continue
            # Pre-flight; on trouble, retry until arm time.
            if not await self._preflight(ev):
                while not self._stop_event.is_set() and self._now() < arm_ts:
                    nxt = min(self._now() + timedelta(seconds=self.retry_interval_seconds), arm_ts)
                    if await self._sleep_until(nxt):
                        break
                    ok, _, _ = await self._is_ready()
                    if ok:
                        log.info("news_straddle scheduler preflight RECOVERED %s", ev.ts)
                        await self._alert("recovered", ev, reason="")
                        break
            if self._stop_event.is_set():
                break
            # Arm at the validated arm time regardless of preflight outcome.
            if await self._sleep_until(arm_ts):
                break
            if ev.status == _PENDING:
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
