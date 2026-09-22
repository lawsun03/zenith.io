"""
Reconciler — periodic broker-vs-internal-state diff.

Why this exists:

  WebSocket feeds drop. Fill events get duplicated. The bot restarts
  mid-session. The user manually closes a position from TopstepX while
  the bot is running. Any of these put the bot's RiskState out of
  sync with broker truth — and the gate's decisions become wrong.

  The reconciler runs every N seconds. It pulls broker positions and
  account balance, compares to RiskState, and:

    - On benign drift (small balance difference): adjust silently and log.
    - On contract drift (open count differs): flatten everything,
      lock out for the session, page the operator.

  The asymmetry is intentional. A balance discrepancy of $5 is probably
  rounding in unrealized P&L. A contract count discrepancy means
  either we missed a fill or placed an order we don't know about — both
  scenarios where continuing to trade is reckless.

What this module is NOT:

  - A replacement for fill events. The reconciler is the *backstop*,
    not the primary path. Fills should arrive via the WebSocket and
    drive RiskState updates immediately. The reconciler catches what
    the WebSocket missed.

  - A position-state owner. RiskState owns position state; the
    reconciler only validates and corrects.

  - A periodic mark-to-market. Equity ticks come on bar closes via
    the broker; the reconciler doesn't touch equity unless a serious
    drift forces a flatten.

Lifecycle:

    reconciler = Reconciler(broker, risk_state, engine,
                            interval_seconds=30)
    await reconciler.start()    # spawns the loop task
    # ... runs forever ...
    await reconciler.stop()     # cancels the task and joins
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from app.sim.protocol import Broker
from app.risk.state import LockoutReason, RiskState

if TYPE_CHECKING:
    from app.sim.events import ExitCoverage
    from app.notifications.email import EmailNotifier

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconcileReport:
    """
    What the reconciler observed on one tick.

    Useful for the dashboard and for tests that want to assert behavior
    across multiple ticks.
    """

    ts: datetime

    # Truth from the broker.
    broker_open_contracts: int
    broker_balance: Decimal

    # What we thought before this tick.
    internal_open_contracts: int
    internal_balance: Decimal

    # Did we take any action?
    drift_detected: bool
    drift_kind: Optional[str]  # "contract_count" | "balance" | "naked_position" | None
    flattened: bool
    notes: str = ""
    naked_instruments: list[str] = field(default_factory=list)


@dataclass
class ReconcilerConfig:
    """Tunable thresholds."""

    # How often to run. 30s is a reasonable starting point — frequent
    # enough to catch drift before it compounds, infrequent enough not
    # to spam the broker with position queries.
    interval_seconds: float = 30.0

    # Balance discrepancies smaller than this are silently corrected.
    # Larger ones flatten and lock out, since "we're $300 off" usually
    # means we missed a fill or executed a phantom one.
    balance_tolerance: Decimal = Decimal("50")

    # First-tick grace: the very first reconcile is allowed to discover
    # any preexisting state without flattening (the bot may have just
    # started up and inherited a position from a prior session). After
    # that, any contract drift is a hard error.
    grace_first_tick: bool = True

    # After the engine places an order, the market fill arrives on the
    # broker's REST API (get_positions) almost instantly, but the fill
    # WebSocket event that updates RiskState.open_contracts can lag by
    # several seconds. If the reconciler ticks during that window it
    # sees broker=1 vs internal=0 and panics. This grace period
    # suppresses the contract-count alarm for N seconds after
    # notify_order_placed() is called. Set to 0 to disable.
    grace_period_after_order_seconds: float = 15.0

    # After a RECONCILE_DRIFT event the emergency flatten brings both sides to 0,
    # which would normally auto-clear the lockout immediately. But the underlying
    # cause (dead WebSocket feed) is still present — clearing instantly lets a
    # new signal fire and drift again. Require this many confirmed-flat seconds
    # before unlocking. Part 2 (feed_is_healthy gate in engine.py) is the primary
    # guard; this is the backstop when feed_is_healthy is unavailable.
    min_flat_after_drift_seconds: float = 120.0

    # --- Exit-coverage monitor ---
    # How long a naked position is tolerated before emergency remediation fires.
    # First sighting → grace started (log + record timestamp, no action).
    # Past this window → _remediate_naked() is called.
    naked_grace_seconds: float = 15.0
    # Per-instrument emergency stop distance in price points (instrument → Decimal).
    # Used by _remediate_naked to place a protective stop at avg_price ± distance.
    emergency_stop_distance: dict[str, Decimal] = field(default_factory=dict)
    # Emergency target expressed as a multiple of the stop distance (R-multiple).
    emergency_target_r: Decimal = Decimal("2.0")


class Reconciler:
    """
    Periodic state validator. Runs as a background task.

    Single instance per ExecutionEngine. Talks to the same Broker and
    RiskState the engine uses; calls back into the engine's flatten path
    when a hard drift is detected.
    """

    def __init__(
        self,
        broker: Broker,
        risk_state: RiskState,
        config: Optional[ReconcilerConfig] = None,
        notifier: Optional["EmailNotifier"] = None,
    ) -> None:
        self.broker = broker
        self.risk_state = risk_state
        self.config = config or ReconcilerConfig()
        self.notifier = notifier

        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self._first_tick_done = False

        # Set by notify_order_placed() when the engine successfully places
        # an order. Used to suppress false-positive contract-count alarms
        # during the fill-event latency window.
        self._last_order_placed_at: Optional[datetime] = None

        # Timestamp of when broker and internal contracts both reached 0 after
        # a RECONCILE_DRIFT flatten. Used to enforce min_flat_after_drift_seconds
        # before the lockout auto-clears.
        self._drift_flat_since: Optional[datetime] = None

        # Last report kept for dashboard inspection.
        self._last_report: Optional[ReconcileReport] = None

        # Per-instrument timestamp of when a position was first seen naked.
        # Drives the grace window before emergency remediation.
        self._naked_since: dict[str, datetime] = {}

    # ------------------------------------------------------------------
    # Read-only views
    # ------------------------------------------------------------------

    @property
    def last_report(self) -> Optional[ReconcileReport]:
        return self._last_report

    def notify_order_placed(self) -> None:
        """
        Called by the ExecutionEngine immediately after broker.place_bracket()
        succeeds. Starts the fill-latency grace window so the next reconciler
        tick does not false-positive on the gap between the REST position
        snapshot (which reflects the fill immediately) and the WebSocket fill
        event (which updates RiskState.open_contracts with some latency).
        """
        self._last_order_placed_at = datetime.now(timezone.utc)
        log.debug(
            "Reconciler: order-placed notified — grace window started "
            "(%.0fs)", self.config.grace_period_after_order_seconds,
        )

    def _is_in_order_grace_period(self, ts: datetime) -> bool:
        """True if we are still within the post-order-placement grace window."""
        if (
            self._last_order_placed_at is None
            or self.config.grace_period_after_order_seconds <= 0
        ):
            return False
        age = (ts - self._last_order_placed_at).total_seconds()
        return age < self.config.grace_period_after_order_seconds

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

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
            # The task wakes from sleep when the stop_event fires.
            # Give it a generous timeout in case it's mid-tick.
            await asyncio.wait_for(self._task, timeout=5.0)
        except asyncio.TimeoutError:
            log.warning("Reconciler stop timed out; cancelling")
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """
        Tick loop. Sleeps `interval_seconds` between checks. Wakes
        early if stop() is called.
        """
        try:
            while not self._stop_event.is_set():
                try:
                    await self.tick()
                except Exception:
                    # A failed tick must NOT kill the loop. The next one
                    # might succeed and recover state.
                    log.exception("Reconciler tick failed")

                # Sleep with cancellation-aware wait. If stop() is
                # called during the sleep, we wake immediately.
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.config.interval_seconds,
                    )
                    break  # stop_event fired
                except asyncio.TimeoutError:
                    pass  # normal — keep ticking
        except asyncio.CancelledError:
            log.info("Reconciler loop cancelled")
            raise

    # ------------------------------------------------------------------
    # One tick
    # ------------------------------------------------------------------

    def _sentinel_report(self, ts: datetime, notes: str) -> ReconcileReport:
        """A tick that happened but produced no broker truth. broker_open_contracts
        = -1 signals 'unknown' so callers never act on it (no drift, no flatten)."""
        report = ReconcileReport(
            ts=ts,
            broker_open_contracts=-1,
            broker_balance=Decimal("0"),
            internal_open_contracts=self.risk_state.open_contracts,
            internal_balance=self.risk_state.realized_balance,
            drift_detected=False,
            drift_kind=None,
            flattened=False,
            notes=notes,
        )
        self._last_report = report
        return report

    async def tick(self) -> ReconcileReport:
        """
        Run one reconcile pass. Returns the report so callers (tests)
        can assert behavior.
        """
        ts = datetime.now(timezone.utc)

        # Don't reconcile against a broker that isn't ready. At startup the
        # TradingSuite isn't created until subscribe() runs, so a pull would
        # raise "not subscribed"; during a feed outage the broker's view is
        # stale and the engine already blocks trading. Skip quietly and let
        # the next tick recover — same philosophy as the query-failure path
        # below: never act on partial data. (PaperBroker reports healthy once
        # connect() has run, so backtests are unaffected.)
        if not self.broker.feed_is_healthy():
            log.debug("Reconciler: broker feed not healthy — skipping tick")
            return self._sentinel_report(ts, "broker feed not healthy")

        # Pull broker truth. If either call fails, abort the tick;
        # we'd rather skip than make decisions on partial data.
        try:
            broker_positions = await self.broker.get_positions()
            broker_balance = await self.broker.account_balance()
        except Exception as e:
            log.warning("Reconciler could not pull broker state: %s", e)
            return self._sentinel_report(ts, f"broker query failed: {e}")

        # Signed net contracts: long = +size, short = -size. The comparison
        # MUST be signed, not magnitude. A sign-flipped/orphaned position
        # (internal short 3 vs broker long 3) has equal magnitude but is a
        # genuine drift — comparing abs() values silently missed it and left
        # an unprotected position open on a live account (2026-06-07 incident).
        broker_contracts = sum(
            (p.size if p.side == "long" else -p.size) for p in broker_positions
        )

        # open_contracts is already signed (negative = short).
        internal_contracts = self.risk_state.open_contracts
        internal_balance = self.risk_state.realized_balance

        # First-tick grace: silently accept whatever we find, but mark
        # ourselves as having seen the first tick so future divergence
        # is treated as drift.
        raw_internal = self.risk_state.open_contracts
        if not self._first_tick_done and self.config.grace_first_tick:
            report = ReconcileReport(
                ts=ts,
                broker_open_contracts=broker_contracts,
                broker_balance=broker_balance,
                internal_open_contracts=raw_internal,
                internal_balance=internal_balance,
                drift_detected=False,
                drift_kind=None,
                flattened=False,
                notes="first tick — grace period, no action taken",
            )
            self._first_tick_done = True
            self._last_report = report
            log.info("Reconciler: first tick complete (grace)")
            return report

        # If a prior drift lockout is in place and contracts now match, auto-clear —
        # but only after holding flat for min_flat_after_drift_seconds. Clearing
        # immediately would allow a new signal to fire while the underlying feed
        # problem (dead WebSocket) is still active.
        if (
            self.risk_state.locked_out is not None
            and self.risk_state.locked_out.code == "RECONCILE_DRIFT"
            and broker_contracts == internal_contracts
        ):
            if self._drift_flat_since is None:
                self._drift_flat_since = ts
            flat_secs = (ts - self._drift_flat_since).total_seconds()
            if flat_secs >= self.config.min_flat_after_drift_seconds:
                log.info(
                    "Reconciler: drift resolved — flat for %.0fs, "
                    "clearing RECONCILE_DRIFT lockout.",
                    flat_secs,
                )
                self.risk_state.locked_out = None
                self._drift_flat_since = None
            else:
                log.debug(
                    "Reconciler: post-drift flat %.0fs/%.0fs — holding lockout.",
                    flat_secs, self.config.min_flat_after_drift_seconds,
                )
        else:
            # Not in drift lockout or contracts diverged again — reset timer.
            self._drift_flat_since = None

        # ----- Contract-count drift: HARD error, flatten + lock out. -----
        if broker_contracts != internal_contracts:
            # Suppress the alarm if we are within the fill-latency grace
            # window. A market order fills almost instantly on the exchange,
            # but the fill WebSocket event that updates open_contracts can
            # lag by seconds. During that window the REST position API
            # already shows the new contract while RiskState still shows
            # the old count — a transient race, not genuine drift.
            if self._is_in_order_grace_period(ts):
                age = (ts - self._last_order_placed_at).total_seconds()  # type: ignore[operator]
                log.warning(
                    "Reconciler: contracts mismatch (internal=%d vs broker=%d) "
                    "within order grace period (age=%.1fs < %.0fs) — "
                    "skipping. Fill event may not have arrived yet.",
                    internal_contracts, broker_contracts,
                    age, self.config.grace_period_after_order_seconds,
                )
                report = ReconcileReport(
                    ts=ts,
                    broker_open_contracts=broker_contracts,
                    broker_balance=broker_balance,
                    internal_open_contracts=raw_internal,
                    internal_balance=internal_balance,
                    drift_detected=False,
                    drift_kind=None,
                    flattened=False,
                    notes=(
                        f"order grace period active (age={age:.1f}s) — "
                        "skipping contract count check"
                    ),
                )
                self._last_report = report
                return report

            report = await self._handle_contract_drift(
                ts=ts,
                broker_contracts=broker_contracts,
                broker_balance=broker_balance,
                internal_contracts=raw_internal,
                internal_balance=internal_balance,
            )
            self._last_report = report
            return report

        # ----- Exit-coverage: counts match, but is every contract protected? -----
        naked_report = await self._check_exit_coverage(ts, broker_positions, broker_balance)
        if naked_report is not None:
            self._last_report = naked_report
            return naked_report

        # ----- Balance drift: tolerated up to threshold. -----
        balance_delta = broker_balance - internal_balance
        if abs(balance_delta) > self.config.balance_tolerance:
            report = await self._handle_balance_drift(
                ts=ts,
                broker_contracts=broker_contracts,
                broker_balance=broker_balance,
                internal_contracts=raw_internal,
                internal_balance=internal_balance,
                delta=balance_delta,
            )
            self._last_report = report
            return report

        # ----- Within tolerance. Nudge balance to broker truth silently. -----
        if balance_delta != Decimal("0"):
            log.debug(
                "Reconciler: minor balance drift %s, adjusting silently",
                balance_delta,
            )
            self.risk_state.realized_balance = broker_balance

        report = ReconcileReport(
            ts=ts,
            broker_open_contracts=broker_contracts,
            broker_balance=broker_balance,
            internal_open_contracts=raw_internal,
            internal_balance=internal_balance,
            drift_detected=False,
            drift_kind=None,
            flattened=False,
        )
        self._last_report = report
        return report

    # ------------------------------------------------------------------
    # Drift handlers
    # ------------------------------------------------------------------

    async def _handle_contract_drift(
        self,
        ts: datetime,
        broker_contracts: int,
        broker_balance: Decimal,
        internal_contracts: int,
        internal_balance: Decimal,
    ) -> ReconcileReport:
        """
        Broker and internal disagree on contract count. The bot's gate
        decisions are based on internal state, so any future order
        could blow risk limits.

        Action: cancel all working orders, flatten every position, lock
        out for the session. The operator must investigate.
        """
        log.error(
            "RECONCILE DRIFT: contracts internal=%d vs broker=%d. "
            "Flattening and locking out.",
            internal_contracts, broker_contracts,
        )

        flattened = await self._emergency_flatten()

        # Sync internal open_contracts to broker truth (0 after a successful
        # flatten). Without this, open_contracts stays at the pre-flatten value
        # forever — the auto-clear condition (broker==internal) never fires and
        # the RECONCILE_DRIFT lockout is permanent. This is safe: we already
        # flattened everything, so internal state should reflect that.
        if flattened:
            self.risk_state.open_contracts = 0
            log.info("Reconciler: internal open_contracts reset to 0 after emergency flatten.")

        # Force lockout regardless of whether flatten succeeded. We do
        # NOT want the engine to consider new entries until a human
        # has reviewed the drift.
        if self.risk_state.locked_out is None:
            self.risk_state.locked_out = LockoutReason(
                code="RECONCILE_DRIFT",
                message=(
                    f"Contract drift: internal={internal_contracts} "
                    f"vs broker={broker_contracts}. Manual review required."
                ),
            )

        if self.notifier is not None and self.notifier.enabled:
            asyncio.create_task(self.notifier.send(
                subject="RECONCILE DRIFT — bot locked out",
                body=(
                    f"Contract count mismatch detected by reconciler.\n\n"
                    f"  Internal contracts: {internal_contracts}\n"
                    f"  Broker contracts:   {broker_contracts}\n\n"
                    f"All positions have been flattened and the bot is locked out.\n"
                    f"Manual review required before trading can resume."
                ),
            ))

        return ReconcileReport(
            ts=ts,
            broker_open_contracts=broker_contracts,
            broker_balance=broker_balance,
            internal_open_contracts=internal_contracts,
            internal_balance=internal_balance,
            drift_detected=True,
            drift_kind="contract_count",
            flattened=flattened,
            notes="emergency flatten + session lockout",
        )

    async def _handle_balance_drift(
        self,
        ts: datetime,
        broker_contracts: int,
        broker_balance: Decimal,
        internal_contracts: int,
        internal_balance: Decimal,
        delta: Decimal,
    ) -> ReconcileReport:
        """
        Balance discrepancy beyond tolerance.

        We adopt broker truth and notify, but do NOT lock out or flatten.
        Rationale: after every exit fill the broker REST balance lags
        by ~30s while the bot's internal balance updates instantly —
        triggering a false-positive lockout on every losing trade. The
        contract-count check already covers the dangerous case (missed
        fill = contracts mismatch). A balance-only discrepancy is always
        a timing artifact and self-corrects on the next tick.
        """
        log.warning(
            "Balance drift: internal=%s vs broker=%s (delta=%s). "
            "Adopting broker truth — no lockout.",
            internal_balance, broker_balance, delta,
        )

        # Adopt broker balance as truth.
        self.risk_state.realized_balance = broker_balance

        if self.notifier is not None and self.notifier.enabled:
            asyncio.create_task(self.notifier.send(
                subject="Balance drift detected (no lockout)",
                body=(
                    f"Balance discrepancy detected by reconciler.\n\n"
                    f"  Internal balance: ${internal_balance}\n"
                    f"  Broker balance:   ${broker_balance}\n"
                    f"  Delta:            ${delta}\n"
                    f"  Tolerance:        ${self.config.balance_tolerance}\n\n"
                    f"Broker balance adopted. Bot continues trading.\n"
                    f"This is usually a timing lag after an exit fill — "
                    f"no action required."
                ),
            ))

        return ReconcileReport(
            ts=ts,
            broker_open_contracts=broker_contracts,
            broker_balance=broker_balance,
            internal_open_contracts=internal_contracts,
            internal_balance=internal_balance,
            drift_detected=True,
            drift_kind="balance",
            flattened=False,
            notes=f"balance delta {delta}, adopted broker truth (no lockout)",
        )

    async def _check_exit_coverage(
        self, ts: datetime, broker_positions: list, broker_balance: Decimal
    ) -> "Optional[ReconcileReport]":
        """For each open position, verify exchange exit coverage. Grace on first
        sighting; remediate once past naked_grace_seconds. Returns a naked report
        if any instrument was remediated this tick, else None.

        Ticks within the grace window are silent — no action is taken until
        naked_grace_seconds has elapsed since first sighting."""
        acted: list[str] = []
        for p in broker_positions:
            if p.size == 0:
                continue
            cov = await self.broker.exit_coverage(p.instrument)
            if cov.fully_covered:
                self._naked_since.pop(p.instrument, None)
                continue
            first = self._naked_since.get(p.instrument)
            if first is None:
                self._naked_since[p.instrument] = ts
                log.warning(
                    "Exit-coverage: %s NAKED (stop %d/%d, target %d/%d) — "
                    "grace started (%.0fs).",
                    p.instrument, cov.covered_stop, cov.position_size,
                    cov.covered_target, cov.position_size,
                    self.config.naked_grace_seconds,
                )
                continue
            if (ts - first).total_seconds() < self.config.naked_grace_seconds:
                continue
            log.error(
                "Exit-coverage: %s STILL NAKED past grace — remediating "
                "(stop %d/%d, target %d/%d).",
                p.instrument, cov.covered_stop, cov.position_size,
                cov.covered_target, cov.position_size,
            )
            await self._remediate_naked(cov)
            self._naked_since.pop(p.instrument, None)
            acted.append(p.instrument)

        # Prune naked-state for instruments no longer open (position closed) so a
        # stale timestamp can't skip the grace window on a future re-entry.
        seen = {p.instrument for p in broker_positions if p.size > 0}
        self._naked_since = {k: v for k, v in self._naked_since.items() if k in seen}

        if not acted:
            return None
        return ReconcileReport(
            ts=ts,
            broker_open_contracts=sum(
                (p.size if p.side == "long" else -p.size) for p in broker_positions
            ),
            broker_balance=broker_balance,
            internal_open_contracts=self.risk_state.open_contracts,
            internal_balance=self.risk_state.realized_balance,
            drift_detected=True,
            drift_kind="naked_position",
            flattened=False,
            naked_instruments=acted,
            notes=f"emergency exit re-attach for {acted}",
        )

    async def _remediate_naked(self, cov: "ExitCoverage") -> None:
        """Re-attach only the missing leg(s). Flatten ONLY if the stop cannot
        be restored — a missing target is not a capital risk."""
        stop_gap = cov.position_size - cov.covered_stop
        target_gap = cov.position_size - cov.covered_target
        dist = self.config.emergency_stop_distance.get(cov.instrument)

        if stop_gap > 0:
            if dist is None:
                log.error(
                    "Exit-coverage: no emergency_stop_distance for %s — "
                    "cannot re-attach a safe stop. Flattening.", cov.instrument,
                )
                await self.broker.flatten(cov.instrument)
                self._notify_naked(cov, action="flattened (no stop distance)")
                return
            stop_price = (
                cov.avg_price - dist if cov.side == "long" else cov.avg_price + dist
            )
            ok = await self.broker.place_protective_stop(
                cov.instrument, stop_gap, stop_price
            )
            if not ok:
                log.error(
                    "Exit-coverage: emergency stop re-attach FAILED on %s — "
                    "flattening.", cov.instrument,
                )
                await self.broker.flatten(cov.instrument)
                self._notify_naked(cov, action="flattened (stop re-attach failed)")
                return

        actions: list[str] = []
        if stop_gap > 0:
            actions.append(f"re-attached stop x{stop_gap}")

        if target_gap > 0:
            if dist is None:
                # Missing target with no distance to price it — not a capital
                # risk, so we don't flatten, but be honest that nothing was done.
                log.error(
                    "Exit-coverage: %s missing target and no emergency distance "
                    "configured — left without a target (no capital risk).",
                    cov.instrument,
                )
                actions.append("target still missing (no distance configured)")
            else:
                target_price = (
                    cov.avg_price + self.config.emergency_target_r * dist
                    if cov.side == "long"
                    else cov.avg_price - self.config.emergency_target_r * dist
                )
                ok = await self.broker.place_protective_target(
                    cov.instrument, target_gap, target_price
                )
                if ok:
                    actions.append(f"re-attached target x{target_gap}")
                else:
                    # No capital risk — log, do NOT flatten.
                    log.error(
                        "Exit-coverage: emergency target re-attach failed on %s "
                        "(non-fatal).", cov.instrument,
                    )
                    actions.append("target re-attach FAILED")

        self._notify_naked(cov, action="; ".join(actions) if actions else "no action")

    def _notify_naked(self, cov: "ExitCoverage", action: str) -> None:
        if self.notifier is not None and self.notifier.enabled:
            asyncio.create_task(self.notifier.send(
                subject=f"NAKED POSITION on {cov.instrument} — {action}",
                body=(
                    f"Exit-coverage monitor found {cov.instrument} unprotected.\n\n"
                    f"  Position size:   {cov.position_size} ({cov.side})\n"
                    f"  Avg price:       {cov.avg_price}\n"
                    f"  Stop covered:    {cov.covered_stop}/{cov.position_size}\n"
                    f"  Target covered:  {cov.covered_target}/{cov.position_size}\n"
                    f"  Action taken:    {action}\n"
                ),
            ))

    async def _emergency_flatten(self) -> bool:
        """
        Cancel all working orders, flatten all positions across all
        instruments the broker reports. Returns True if we believe the
        flatten succeeded (no positions remaining at end).

        Best-effort: any individual call may fail; we keep going and
        let the next reconciler tick verify.
        """
        try:
            await self.broker.cancel_all()
        except Exception:
            log.exception("cancel_all failed during emergency flatten")

        try:
            positions = await self.broker.get_positions()
        except Exception:
            log.exception("get_positions failed during emergency flatten")
            return False

        instruments = {p.instrument for p in positions}
        for instrument in instruments:
            try:
                await self.broker.flatten(instrument)
            except Exception:
                log.exception(
                    "flatten failed for %s during emergency flatten",
                    instrument,
                )

        # Verify.
        try:
            after = await self.broker.get_positions()
            return len(after) == 0
        except Exception:
            return False
