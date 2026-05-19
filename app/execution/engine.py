"""
ExecutionEngine — the full live loop.

Responsibilities:

  1. Subscribe to broker events (bar / fill / equity).
  2. On each bar, run the strategy detectors and compose a signal.
  3. Run any signal through the pretrade risk gate.
  4. If allowed, place a bracket via the broker.
  5. On fills and equity ticks, update risk state.
  6. On lockout transitions, flatten any open position.

What this module is NOT:
  - Order management beyond initial placement. Brackets are atomic at
    the broker; we don't track or modify the children separately.
    The reconciler (separate module, next ship) is the safety net.
  - A backtester. The engine runs against ANY Broker — paper, demo,
    or live — but doesn't care about replay speed or cost modeling.
    That's the backtester's job on DigitalOcean.

Idempotency note: every event handler is async and may be called
concurrently. The engine uses a single asyncio.Lock around the
critical section (gate + place + state update) so a fill arriving
mid-decision can't corrupt state. Without this lock, a tight burst
of bars + fills would race.

Lifecycle:

    engine = ExecutionEngine(broker, risk_state, strategy_runner)
    await engine.start()    # registers handlers
    # ... runs forever ...
    await engine.stop()     # graceful shutdown
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable, Optional

from app.broker.events import Bar, Fill, MarkToMarket
from app.broker.protocol import Broker
from app.bot_config import StrategyParams
from app.risk.pretrade import Allow, Deny, ProposedOrder, check
from app.risk.state import RiskState
from app.strategy.composer import Signal, SweepDisplacementComposer
from app.strategy.displacement import DisplacementDetector
from app.strategy.liquidity import LiquidityTracker
from app.strategy.volume_profile import VolumeProfileTracker

log = logging.getLogger(__name__)


def _tf_seconds(timeframe: str) -> int:
    """Parse a timeframe string like '1min', '5min', '1h' → seconds."""
    tf = timeframe.strip().lower()
    if tf.endswith("h"):
        return int(tf[:-1]) * 3600
    if tf.endswith("min"):
        return int(tf[:-3]) * 60
    if tf.endswith("d"):
        return int(tf[:-1]) * 86400
    return 60  # fallback: 1 min


# Optional callback for journal/dashboard emission. Decoupled from the
# engine so adding a journal later doesn't change the engine API.
SignalEmitted = Callable[[Signal, "OrderOutcome"], Awaitable[None]]


@dataclass(frozen=True)
class OrderOutcome:
    """What happened when we tried to act on a signal."""

    placed: bool
    reason: str                  # "allowed" | denial code | "broker rejected"
    allowed_size: int = 0
    broker_order_id: str | None = None


@dataclass
class StrategyRunner:
    """
    One instrument's full strategy stack: liquidity + displacement +
    composer. Bundled so the engine deals in one object per instrument.

    This is the place to swap detectors if you want to A/B different
    parameters per instrument without changing the engine.
    """

    instrument: str
    liquidity: LiquidityTracker
    displacement: DisplacementDetector
    composer: SweepDisplacementComposer
    vp: VolumeProfileTracker | None = None

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        """Run all detectors against one bar. Returns at most one Signal."""
        sweeps = self.liquidity.on_bar(bar)
        for s in sweeps:
            self.composer.on_sweep(bar, s)

        signal: Optional[Signal] = None
        disp = self.displacement.on_bar(bar)
        if disp is not None:
            signal = self.composer.on_displacement(bar, disp)

        # Bookkeeping AFTER signal evaluation — see composer docstring.
        self.composer.on_bar_close(bar)
        return signal


class ExecutionEngine:
    """
    The wire. Connects broker → strategy → risk → broker again.

    One engine instance per process. If you want to trade multiple
    instruments, register multiple StrategyRunners on one engine.
    """

    def __init__(
        self,
        broker: Broker,
        risk_state: RiskState,
        runners: list[StrategyRunner],
        on_signal: SignalEmitted | None = None,
        on_order_placed: Callable[[], None] | None = None,
        replay_mode: bool = False,
        contracts: int = 1,
        strategy_cfg: "StrategyParams | None" = None,
    ) -> None:
        self.broker = broker
        self.risk_state = risk_state
        self.runners = {r.instrument: r for r in runners}
        self.on_signal = on_signal
        self.contracts = contracts  # contracts per signal; hot-applied via PATCH /api/config
        self.strategy_cfg = strategy_cfg
        # Called immediately after broker.place_bracket() succeeds so the
        # reconciler can start its fill-latency grace window.
        self._on_order_placed = on_order_placed

        # When True, the wall-clock staleness check is skipped so historical
        # bars are processed the same way regardless of when the run happens.
        # Always True in backtests; False in live/paper streaming.
        self._replay_mode = replay_mode

        # Track the last lockout state we acted on. If the state
        # transitions IN to locked while we have positions open, we
        # flatten. The engine never tries to flatten twice.
        self._was_locked: bool = False

        # Reentrancy guard for lockout flattening. broker.flatten()
        # emits a fill which routes through _handle_fill which calls
        # _check_lockout_transition again — without this flag we'd
        # try to flatten the same position multiple times concurrently.
        self._flattening: bool = False

        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        self.broker.on_bar(self._handle_bar)
        self.broker.on_fill(self._handle_fill)
        self.broker.on_equity(self._handle_equity)
        self._started = True
        log.info(
            "ExecutionEngine started: %d instruments tracked",
            len(self.runners),
        )

    async def stop(self) -> None:
        """Graceful shutdown: cancel all working orders, do not flatten."""
        if not self._started:
            return
        # Cancel only — we do NOT flatten on stop. The operator may be
        # restarting the bot mid-position; auto-flattening would be
        # surprising. The kill-switch endpoint is for that.
        try:
            await self.broker.cancel_all()
        except Exception as e:
            log.warning("cancel_all on stop failed: %s", e)
        self._started = False

    # ------------------------------------------------------------------
    # Event handlers — registered with the broker
    # ------------------------------------------------------------------

    async def _handle_bar(self, bar: Bar) -> None:
        """
        Bar arrived. Run the runner, evaluate any signal, place if allowed.

        No lock here: asyncio is single-threaded and risk_state
        mutations are synchronous, so the only place coroutines can
        interleave is at await points. The only await between gate
        check and order placement is broker.place_bracket itself,
        and a fill arriving during that window only changes state
        the NEXT signal will see — which is correct behavior.
        """
        runner = self.runners.get(bar.instrument)
        if runner is None:
            # We're subscribed to a symbol we don't have a runner for.
            # Either misconfiguration or a multi-runner setup in
            # progress. Log once and ignore — don't crash.
            return

        # Staleness guard: bars older than 2× the timeframe are warmup
        # data only — process them for indicator state but don't trade.
        # Skipped in replay_mode because historical bars are always "old"
        # relative to wall clock; comparing them to now() would suppress
        # every signal and make backtest results vary by run time.
        if self._replay_mode:
            is_stale = False
        else:
            tf_secs = _tf_seconds(bar.timeframe)
            age_secs = (datetime.now(timezone.utc) - bar.ts).total_seconds()
            is_stale = age_secs > 2 * tf_secs

        # Feed bar into VP tracker — must happen before strategy evaluation
        # so the profile is current when apply() is called this same bar.
        # VP accumulates even for stale/warmup bars.
        if runner.vp is not None and self.strategy_cfg is not None:
            runner.vp.on_bar(bar, self.strategy_cfg)

        try:
            signal = runner.on_bar(bar)
        except Exception:
            log.exception("Strategy raised on bar %s", bar.ts)
            return

        if signal is None or is_stale:
            if is_stale and signal is not None:
                log.debug(
                    "Warmup bar %s (age=%.0fs) generated signal — skipping order.",
                    bar.ts, (datetime.now(timezone.utc) - bar.ts).total_seconds(),
                )
            return

        # VP gate: filter + target override. Runs before pretrade risk check.
        if (
            runner.vp is not None
            and self.strategy_cfg is not None
            and self.strategy_cfg.vp_enabled
            and runner.vp.has_prior_profile()
        ):
            signal = runner.vp.apply(signal, self.strategy_cfg)
            if signal is None:
                return  # VP filter rejected — already logged in apply()

        outcome = await self._act_on_signal(signal)
        if self.on_signal is not None:
            # Best-effort: a journaling failure shouldn't stop trading.
            try:
                await self.on_signal(signal, outcome)
            except Exception:
                log.exception("on_signal callback raised")

    async def _handle_fill(self, fill: Fill) -> None:
        """Fill arrived. Update risk state. Synchronous, no await needed."""
        self.risk_state.record_fill(
            realized_pnl_delta=fill.realized_pnl_delta,
            contracts_delta=fill.contracts_delta,
            ts=fill.ts,
        )
        log.info(
            "Fill: %s %s %d @ %s pnl=%s contracts_now=%d",
            fill.instrument,
            "ENTRY" if fill.is_entry else "EXIT",
            fill.size,
            fill.fill_price,
            fill.realized_pnl_delta,
            self.risk_state.open_contracts,
        )

    async def _handle_equity(self, mtm: MarkToMarket) -> None:
        """
        Mark-to-market arrived. Update high-water and check lockouts.
        If we just transitioned INTO lockout with open positions,
        flatten them at market.
        """
        self.risk_state.mark_equity(mtm.equity, mtm.ts)
        await self._check_lockout_transition()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _act_on_signal(self, signal: Signal) -> OrderOutcome:
        """Run the pretrade gate and place if allowed. Caller holds the lock."""
        order = ProposedOrder(
            instrument=signal.instrument,
            side=signal.side,
            size=self.contracts,
            entry=signal.entry,
            stop=signal.stop,
            target=signal.target,
            is_entry=True,
        )
        decision = check(order, self.risk_state)

        if isinstance(decision, Deny):
            log.info(
                "Signal denied: %s — %s (%s)",
                signal.rationale, decision.reason_code, decision.message,
            )
            return OrderOutcome(placed=False, reason=decision.reason_code)

        # Prime the reconciler grace window BEFORE sending to exchange.
        # Market orders fill in microseconds; the reconciler can tick during
        # the HTTP round-trip and see broker=N, internal=0 before we call
        # notify_order_placed() — triggering a false-positive drift. Starting
        # the grace window here ensures it is active before the order hits the
        # exchange. If the broker rejects, the grace runs harmlessly (no
        # position opened, so no contract drift to alarm on).
        if self._on_order_placed is not None:
            self._on_order_placed()

        # Allowed — place the bracket. Note: the gate may have sized down,
        # which is reflected in decision.allowed_size.
        result = await self.broker.place_bracket(
            instrument=signal.instrument,
            side=signal.side,
            size=decision.allowed_size,
            entry=signal.entry,
            stop=signal.stop,
            target=signal.target,
        )

        if not result.success:
            log.warning(
                "Broker rejected bracket: %s (%s)",
                signal.rationale, result.error,
            )
            return OrderOutcome(
                placed=False,
                reason=f"broker rejected: {result.error}",
                allowed_size=decision.allowed_size,
            )

        log.info(
            "Bracket placed: %s size=%d entry=%s stop=%s target=%s",
            signal.rationale,
            decision.allowed_size,
            signal.entry, signal.stop, signal.target,
        )
        return OrderOutcome(
            placed=True,
            reason="allowed",
            allowed_size=decision.allowed_size,
            broker_order_id=result.entry_order_id,
        )

    async def _check_lockout_transition(self) -> None:
        """
        If we just entered lockout AND have open contracts, flatten.
        Protects against the case where MLL trips mid-trade — we want
        to be flat ASAP, not wait for the bracket's stop to fill.

        Reentrancy: broker.flatten() emits a fill which routes back
        through _handle_fill → _handle_equity → here. The _flattening
        flag prevents recursion.
        """
        if self._flattening:
            return

        is_locked = self.risk_state.locked_out is not None

        if is_locked and not self._was_locked:
            # Just transitioned in.
            log.warning(
                "LOCKOUT: %s. open_contracts=%d",
                self.risk_state.locked_out.message,
                self.risk_state.open_contracts,
            )
            if self.risk_state.open_contracts != 0:
                self._flattening = True
                try:
                    # Cancel children first so the market flatten doesn't
                    # race with the bracket's stop.
                    for instrument in self.runners:
                        try:
                            await self.broker.cancel_all(instrument)
                        except Exception:
                            log.exception("cancel_all during lockout failed")
                        try:
                            await self.broker.flatten(instrument)
                        except Exception:
                            log.exception("flatten during lockout failed")
                finally:
                    self._flattening = False

        self._was_locked = is_locked
