"""
Journal — in-memory event log for the dashboard.

Three rolling buffers:
  - Signals (with their outcomes)
  - Fills
  - Reconcile reports (last N for trend)

Bounded sizes — this is for the dashboard, not durable storage. The
DigitalOcean sync layer (separate ship) will handle durable journaling
to your existing FastAPI backend.

Thread-safety: not strictly needed since asyncio is single-threaded and
all writes happen in event handlers. But we use a simple lock anyway
because the WebSocket broadcaster reads concurrently with bar handlers,
and a tear during read could yield half-formed dicts.
"""

from __future__ import annotations

import asyncio
import csv
import logging
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Deque, TYPE_CHECKING

log = logging.getLogger(__name__)

from app.broker.events import Bar, Fill
from app.execution.engine import OrderOutcome
from app.execution.reconciler import ReconcileReport
from app.strategy.composer import Signal

if TYPE_CHECKING:
    from app.sync.outbox import Outbox
    from app.strategy.grader import SetupGrade


def _decimal_to_str(obj: Any) -> Any:
    """Recursively convert Decimal → str for JSON serialization.
    Strings preserve precision; floats lose it. The frontend converts
    back to Number where it needs to do math."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _decimal_to_str(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_decimal_to_str(v) for v in obj]
    return obj


@dataclass
class JournalEntry:
    """A row in the journal — kind discriminates the payload."""

    ts: datetime
    kind: str        # "signal" | "fill" | "reconcile"
    payload: dict


class Journal:
    """In-memory event collector + a queue of newly-emitted entries
    that the WebSocket broadcaster drains.

    If `outbox` is provided, every recorded entry is also enqueued for
    durable shipment to DO via the sender worker. The outbox is
    optional — paper mode and test setups can skip it.
    """

    def __init__(
        self,
        max_signals: int = 100,
        max_fills: int = 200,
        max_reconciles: int = 50,
        outbox: "Outbox | None" = None,
    ) -> None:
        self._signals: Deque[JournalEntry] = deque(maxlen=max_signals)
        self._fills: Deque[JournalEntry] = deque(maxlen=max_fills)
        self._reconciles: Deque[JournalEntry] = deque(maxlen=max_reconciles)
        self._lock = asyncio.Lock()
        self._outbox = outbox

        # New-entry subscribers: each WebSocket connection adds a queue;
        # we push every new entry to every queue. Bounded queues drop
        # the oldest message under backpressure rather than blocking
        # event handlers.
        self._subscribers: list[asyncio.Queue[JournalEntry]] = []

    # ------------------------------------------------------------------
    # Writes — called from engine event handlers
    # ------------------------------------------------------------------

    async def record_signal(self, signal: Signal, outcome: OrderOutcome) -> None:
        """Engine's on_signal callback funnels here."""
        entry = JournalEntry(
            ts=signal.created_at,
            kind="signal",
            payload=_decimal_to_str({
                "instrument": signal.instrument,
                "side": signal.side,
                "entry": signal.entry,
                "stop": signal.stop,
                "target": signal.target,
                "killzone": signal.killzone,
                "rationale": signal.rationale,
                "sweep_pattern": signal.sweep_pattern,
                "fvg_low": signal.fvg_low,
                "fvg_high": signal.fvg_high,
                "setup_grade": signal.setup_grade.grade if signal.setup_grade else None,
                "outcome": {
                    "placed": outcome.placed,
                    "reason": outcome.reason,
                    "allowed_size": outcome.allowed_size,
                    "broker_order_id": outcome.broker_order_id,
                },
            }),
        )
        async with self._lock:
            self._signals.append(entry)
        self._publish(entry)
        self._enqueue_outbox(entry)

    async def record_fill(self, fill: Fill) -> None:
        """Subscribe to broker.on_fill in main."""
        # Provisional fills are the early-arrival fanout used to keep risk
        # state's open_contracts in sync before the broker knows whether the
        # fill is an entry or an exit. A corrected fanout (with the real
        # is_entry/realized_pnl) follows from entry-replay or
        # _reprocess_early_exit. Skip the provisional one so it doesn't
        # appear as a phantom ENTRY row in the dashboard or get shipped to
        # the durable outbox.
        if getattr(fill, "is_provisional", False):
            return
        entry = JournalEntry(
            ts=fill.ts,
            kind="fill",
            payload=_decimal_to_str({
                "instrument": fill.instrument,
                "side": fill.side,
                "fill_price": fill.fill_price,
                "size": fill.size,
                "is_entry": fill.is_entry,
                "realized_pnl_delta": fill.realized_pnl_delta,
                "broker_order_id": fill.broker_order_id,
            }),
        )
        async with self._lock:
            self._fills.append(entry)
        self._publish(entry)
        self._enqueue_outbox(entry)

    async def record_reconcile(self, report: ReconcileReport) -> None:
        """Called periodically by a small wrapper around reconciler.tick."""
        entry = JournalEntry(
            ts=report.ts,
            kind="reconcile",
            payload=_decimal_to_str({
                "broker_open_contracts": report.broker_open_contracts,
                "broker_balance": report.broker_balance,
                "internal_open_contracts": report.internal_open_contracts,
                "internal_balance": report.internal_balance,
                "drift_detected": report.drift_detected,
                "drift_kind": report.drift_kind,
                "flattened": report.flattened,
                "notes": report.notes,
            }),
        )
        async with self._lock:
            self._reconciles.append(entry)
        self._publish(entry)
        self._enqueue_outbox(entry)

    def _enqueue_outbox(self, entry: JournalEntry) -> None:
        """
        Best-effort enqueue. Outbox.enqueue catches its own errors;
        we wrap once more here to be paranoid — a bad outbox must never
        crash the trading event handlers.
        """
        if self._outbox is None:
            return
        try:
            self._outbox.enqueue(
                kind=entry.kind,
                payload={"ts": entry.ts.isoformat(), **entry.payload},
            )
        except Exception:
            # Outbox already logs internally; this is double-defense.
            pass

    # ------------------------------------------------------------------
    # Reads — called from HTTP/WebSocket handlers
    # ------------------------------------------------------------------

    def bootstrap_fills_from_csv(self, path: Path) -> None:
        """
        Load today's persisted fills from the daily CSV into _fills at startup.

        Called once after a restart so the EOD summary covers fills from before
        the restart. Does not publish to SSE subscribers (none exist yet at
        startup) or enqueue to outbox (already durable in the CSV).
        """
        if not path.exists():
            return
        loaded = 0
        try:
            with path.open(newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        ts = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
                        entry = JournalEntry(
                            ts=ts,
                            kind="fill",
                            payload={
                                "instrument": row["instrument"],
                                "side": row["side"],
                                "fill_price": row["fill_price"],
                                "size": int(row["size"]),
                                "is_entry": row["type"] == "ENTRY",
                                "realized_pnl_delta": row["realized_pnl"],
                                "broker_order_id": row["broker_order_id"],
                            },
                        )
                        self._fills.append(entry)
                        loaded += 1
                    except Exception:
                        log.warning(
                            "bootstrap_fills_from_csv: skipped malformed row in %s",
                            path, exc_info=True,
                        )
        except Exception:
            log.warning("bootstrap_fills_from_csv: could not read %s", path, exc_info=True)
        if loaded:
            log.info("Bootstrapped %d fill(s) from %s into journal", loaded, path)

    def reset(self) -> None:
        """Clear all journal entries and notify WebSocket clients."""
        self._signals.clear()
        self._fills.clear()
        self._reconciles.clear()
        control = JournalEntry(
            ts=datetime.now(timezone.utc),
            kind="reset",
            payload={},
        )
        self._publish(control)

    async def recent_signals(self, limit: int = 50) -> list[dict]:
        async with self._lock:
            entries = list(self._signals)[-limit:]
        return [self._serialize(e) for e in reversed(entries)]

    async def recent_fills(self, limit: int = 50) -> list[dict]:
        async with self._lock:
            entries = list(self._fills)[-limit:]
        return [self._serialize(e) for e in reversed(entries)]

    async def recent_reconciles(self, limit: int = 20) -> list[dict]:
        async with self._lock:
            entries = list(self._reconciles)[-limit:]
        return [self._serialize(e) for e in reversed(entries)]

    @staticmethod
    def _serialize(entry: JournalEntry) -> dict:
        return {
            "ts": entry.ts.isoformat(),
            "kind": entry.kind,
            "payload": entry.payload,
        }

    # ------------------------------------------------------------------
    # WebSocket pub-sub
    # ------------------------------------------------------------------

    def publish_bar(self, bar: Bar, display_instrument: str | None = None) -> None:
        """Stream a bar to WebSocket subscribers without storing it."""
        entry = JournalEntry(
            ts=bar.ts,
            kind="bar",
            payload={
                "instrument": display_instrument if display_instrument is not None else bar.instrument,
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "volume": bar.volume,
            },
        )
        self._publish(entry)

    def publish_strategy_state(
        self,
        instrument: str,
        grade: "SetupGrade | None" = None,
        active_fvgs_count: int = 0,
        session_high: "Decimal | None" = None,
        session_low: "Decimal | None" = None,
        in_macro: bool = False,
        news_blackout: bool = False,
        phase: "dict | None" = None,
        orb_state: "dict | None" = None,
    ) -> None:
        """Emit strategy_state WebSocket event each bar for the live dashboard."""
        payload: dict = {"instrument": instrument}

        if grade is not None:
            payload.update({
                "grade": grade.grade,
                "passes": grade.passes,
                "has_delivery_fvg": grade.has_delivery_fvg,
                "delivery_fvg_side": grade.delivery_fvg_side,
                "delivery_fvg_in_pd": grade.delivery_fvg_in_pd,
                "premium_discount_ok": grade.premium_discount_ok,
                "target_clear": grade.target_clear,
                "fvg_singular": grade.fvg_singular,
                "singularity_timeframe": grade.singularity_timeframe,
                "momentum_quality": grade.momentum_quality,
                "bpr_confluence": grade.bpr_confluence,
                "bpr_timeframe": grade.bpr_timeframe,
                "recent_sweep_ok": grade.recent_sweep_ok,
                "fib_displacement_ok": grade.fib_displacement_ok,
                "fib_extension": str(grade.fib_extension),
                "ce_respected": grade.ce_respected,
                "reason": grade.reason,
            })

        payload["active_fvgs_count"] = active_fvgs_count
        if session_high is not None:
            payload["session_high"] = str(session_high)
        if session_low is not None:
            payload["session_low"] = str(session_low)
        payload["in_macro_window"] = in_macro
        payload["news_blackout"] = news_blackout
        payload["phase"] = phase  # None when practice; dict with tracker state otherwise
        if orb_state is not None:
            payload["orb_state"] = orb_state

        entry = JournalEntry(
            ts=datetime.now(timezone.utc),
            kind="strategy_state",
            payload=_decimal_to_str(payload),
        )
        self._publish(entry)

    def subscribe(self) -> asyncio.Queue[JournalEntry]:
        """Add a new subscriber. Caller MUST call unsubscribe when done."""
        # Larger queue to handle bar streaming during fast replay.
        q: asyncio.Queue[JournalEntry] = asyncio.Queue(maxsize=2000)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[JournalEntry]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def _publish(self, entry: JournalEntry) -> None:
        """Best-effort fanout. Full queues drop the message; the client
        will catch up on the next event or via initial-state load."""
        for q in self._subscribers:
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                # Pop the oldest so the new one fits — clients prefer
                # fresh data over stale data.
                try:
                    q.get_nowait()
                    q.put_nowait(entry)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
