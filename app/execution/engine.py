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
import dataclasses
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable, Optional

from dataclasses import replace as dc_replace

from app.broker.events import Bar, Fill, MarkToMarket
from app.broker.protocol import Broker
from app.broker.pricing import _point_value
from app.bot_config import StrategyParams
from app.risk.flatten import in_flatten_window, past_entry_cutoff
from app.risk.pretrade import Allow, Deny, ProposedOrder, check
from app.risk.sizing import risk_based_size
from app.risk.state import RiskState
from app.strategy.armed_zone import ArmedZone, ArmedZoneTracker
from app.strategy.composer import Signal, SweepDisplacementComposer
from app.strategy.displacement import DisplacementDetector, DisplacementEvent
from app.strategy.grader import SetupGrader
from app.strategy.killzone import in_killzone, in_macro_window, in_news_blackout
from app.strategy.liquidity import LiquidityTracker
from app.strategy.volume_profile import VolumeProfileTracker

log = logging.getLogger(__name__)


def _is_opposite_side(signal_side: str, open_contracts: int) -> bool:
    """True when the signal direction conflicts with the current open position."""
    return (signal_side == "long" and open_contracts < 0) or \
           (signal_side == "short" and open_contracts > 0)


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

# Called BEFORE await broker.place_bracket() so signal meta is available
# even if the market-order fill races in during the HTTP round-trip.
PrePlaceCallback = Callable[[Signal, int], Awaitable[None]]


@dataclass(frozen=True)
class OrderOutcome:
    """What happened when we tried to act on a signal."""

    placed: bool
    reason: str                  # "allowed" | denial code | "broker rejected"
    allowed_size: int = 0
    broker_order_id: str | None = None


@dataclass(frozen=True)
class RejectInfo:
    """A setup the runner rejected internally (grader-B, premature liquidity, or
    zone invalidation). Surfaced to the rejection ledger via ExecutionEngine.on_reject."""

    reason: str                  # "grader_<G>" | "premature_liquidity" | "invalidated"
    side: str
    entry: Decimal | None
    stop: Decimal | None
    target: Decimal | None
    grade: str
    score: int = 0
    killzone: str = ""
    rationale: str = ""


@dataclass
class StrategyRunner:
    """
    One instrument's full strategy stack: liquidity + displacement +
    composer. Bundled so the engine deals in one object per instrument.

    This is the place to swap detectors if you want to A/B different
    parameters per instrument without changing the engine.
    """

    instrument: str
    timeframe: str
    liquidity: LiquidityTracker
    displacement: DisplacementDetector
    composer: SweepDisplacementComposer
    grader: SetupGrader
    strategy_cfg: StrategyParams
    armed_tracker: ArmedZoneTracker = field(default_factory=ArmedZoneTracker)
    _pending_signal: Optional[Signal] = field(default=None, init=False, repr=False)
    vp: VolumeProfileTracker | None = None
    signal_instrument: str = ""  # if set, bars from this instrument drive signals; execution uses `instrument`
    _prev_atr: Decimal | None = field(default=None, init=False, repr=False)
    last_reject: "RejectInfo | None" = field(default=None, init=False, repr=False)

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        """Run all detectors against one bar. Returns at most one Signal."""
        # Cleared each bar; set at an internal reject site below. The engine reads
        # it immediately after on_bar(), before the next bar clears it.
        self.last_reject = None
        # News and session filters (Rules G, H)
        if in_news_blackout(bar.ts, self.strategy_cfg.ifvg_news_blackout):
            log.info("Signal blocked: news blackout at %s", bar.ts)
            return None
        signal: Optional[Signal] = None

        # ── Armed zone path ──────────────────────────────────────────────
        if self.armed_tracker.active is not None and self._pending_signal is not None:
            zone = self.armed_tracker.active  # capture before on_bar clears it

            # Rule F: premature liquidity — TP1 hit before entry fills (config-gated)
            if self.strategy_cfg.ifvg_rule_f_enabled and zone.tp1_price is not None:
                if zone.side == "long" and bar.high >= zone.tp1_price:
                    log.info(
                        "Premature liquidity: TP1 %s hit before long entry — cancelling armed zone",
                        zone.tp1_price,
                    )
                    _ps = self._pending_signal
                    self.armed_tracker.cancel()
                    self._pending_signal = None
                    self.last_reject = RejectInfo(
                        reason="premature_liquidity", side="long",
                        entry=zone.entry_price, stop=zone.stop_price, target=zone.tp1_price,
                        grade=(_ps.setup_grade.grade if _ps and _ps.setup_grade else ""),
                        score=(_ps.setup_grade.score if _ps and _ps.setup_grade else 0),
                        killzone=(_ps.killzone if _ps else "") or "",
                        rationale=(_ps.rationale if _ps else "") or "",
                    )
                    # fall through to normal signal generation
                elif zone.side == "short" and bar.low <= zone.tp1_price:
                    log.info(
                        "Premature liquidity: TP1 %s hit before short entry — cancelling armed zone",
                        zone.tp1_price,
                    )
                    _ps = self._pending_signal
                    self.armed_tracker.cancel()
                    self._pending_signal = None
                    self.last_reject = RejectInfo(
                        reason="premature_liquidity", side="short",
                        entry=zone.entry_price, stop=zone.stop_price, target=zone.tp1_price,
                        grade=(_ps.setup_grade.grade if _ps and _ps.setup_grade else ""),
                        score=(_ps.setup_grade.score if _ps and _ps.setup_grade else 0),
                        killzone=(_ps.killzone if _ps else "") or "",
                        rationale=(_ps.rationale if _ps else "") or "",
                    )
                    # fall through to normal signal generation

        if self.armed_tracker.active is not None and self._pending_signal is not None:
            zone = self.armed_tracker.active
            status = self.armed_tracker.on_bar(bar)

            if status == "filled":
                # Build execution signal: zone's entry/stop, pending signal's target+meta
                pending = self._pending_signal
                self._pending_signal = None
                signal = dc_replace(
                    pending,
                    entry=zone.entry_price,
                    stop=zone.stop_price,
                    armed_zone=zone,
                )
                log.info(
                    "Armed zone filled: %s %s @ entry=%s stop=%s",
                    zone.killzone, zone.side, zone.entry_price, zone.stop_price,
                )
            elif status in ("invalidated", "expired"):
                log.info("Armed zone %s — pending signal discarded", status)
                _ps = self._pending_signal
                self._pending_signal = None
                self.last_reject = RejectInfo(
                    reason=("zone_expired" if status == "expired" else "invalidated"),
                    side=(_ps.side if _ps else ""),
                    entry=(_ps.entry if _ps else None), stop=(_ps.stop if _ps else None),
                    target=(_ps.target if _ps else None),
                    grade=(_ps.setup_grade.grade if _ps and _ps.setup_grade else ""),
                    score=(_ps.setup_grade.score if _ps and _ps.setup_grade else 0),
                    killzone=(_ps.killzone if _ps else "") or "",
                    rationale=(_ps.rationale if _ps else "") or "",
                )
                # fall through to normal signal generation on this same bar

            elif status == "pending":
                # Zone still live — skip signal generation this bar
                self._prev_atr = self.displacement.atr
                kz = in_killzone(bar.ts, self.composer._zones)
                self.grader.update_session_range(bar, kz.name if kz else None)
                self.composer.on_bar_close(bar)
                return None

        # ── Normal signal generation (when no active zone or just invalidated) ──
        if signal is None:
            sweeps = self.liquidity.on_bar(bar, atr=self._prev_atr)
            for sw in sweeps:
                self.composer.on_sweep(bar, sw)

            disp = self.displacement.on_bar(bar)
            if disp is not None:
                candidate = self.composer.on_displacement(bar, disp)
                if candidate is not None:
                    grade = self.grader.score(
                        candidate,
                        disp,
                        self.displacement.active_fvgs,
                        bars_since_sweep=0,  # sweep tracking deferred — always 0 for now
                        sweep_window_bars=self.strategy_cfg.ifvg_sweep_window_bars,
                        min_displacement_mult=self.strategy_cfg.ifvg_min_displacement_mult,
                        min_grade=self.strategy_cfg.grader_min_grade,
                        gapping_sack_enabled=self.strategy_cfg.ifvg_gapping_sack_enabled,
                    )
                    if grade.passes:
                        graded = dc_replace(candidate, setup_grade=grade)
                        signal = self._arm_or_return(bar, graded, disp)
                    else:
                        log.info(
                            "Signal filtered by grader: %s — %s",
                            grade.grade, grade.reason,
                        )
                        self.last_reject = RejectInfo(
                            reason=f"grader_{grade.grade}", side=candidate.side,
                            entry=candidate.entry, stop=candidate.stop,
                            target=candidate.target, grade=grade.grade,
                            score=grade.score,
                            killzone=candidate.killzone or "",
                            rationale=candidate.rationale or "",
                        )

        # Cache ATR for the NEXT bar's liquidity call (one-bar lag is acceptable;
        # ATR doesn't change sharply bar-to-bar and liquidity runs before displacement).
        self._prev_atr = self.displacement.atr

        # Update grader session range every bar
        kz = in_killzone(bar.ts, self.composer._zones)
        self.grader.update_session_range(bar, kz.name if kz else None)

        # Macro-window bonus log (observability, not a hard filter)
        if signal and in_macro_window(bar.ts, self.strategy_cfg.ifvg_macro_windows):
            log.info("Signal in macro window — higher-confidence timing")

        # Bookkeeping AFTER signal evaluation — see composer docstring.
        self.composer.on_bar_close(bar)
        return signal

    def _arm_or_return(self, bar: Bar, signal: Signal, disp: DisplacementEvent) -> Optional[Signal]:
        """
        For 'close' mode: return signal immediately (existing behavior).
        For 'ifvg_edge' / 'retrace_ce': arm the tracker and return None.

        Stop buffer is sourced from the composer config (already in price units)
        to avoid a tick-size conversion here.
        """
        mode = self.strategy_cfg.ifvg_entry_mode

        if signal.fvg_low is None or signal.fvg_high is None:
            # No FVG zone bounds — can't compute armed zone; return signal directly
            log.info("Armed zone skipped: no fvg_low/fvg_high on signal — returning directly")
            return signal

        if mode == "close":
            # Immediate fill at signal's original entry — unchanged from previous behavior.
            return signal

        # Compute TP1 for premature-liquidity cancel (Rule F)
        tp1_price: Optional[Decimal] = None
        highs = self.grader._htf_swing_highs
        lows = self.grader._htf_swing_lows
        if signal.side == "long":
            candidates = [h for h in highs if h > signal.entry]
            tp1_price = min(candidates) if candidates else None
        else:
            candidates = [l for l in lows if l < signal.entry]
            tp1_price = max(candidates) if candidates else None

        # Use composer's stop_buffer (already in price units) as zone stop buffer
        stop_buffer = self.composer.config.stop_buffer

        zone = self.armed_tracker.arm(
            side=signal.side,
            fvg_low=signal.fvg_low,
            fvg_high=signal.fvg_high,
            entry_mode=mode,
            stop_buffer=stop_buffer,
            created_at=bar.ts,
            killzone=signal.killzone,
            sweep_extreme=signal.sweep_extreme,
            max_age_bars=self.strategy_cfg.ifvg_zone_max_age_bars,
        )
        # Attach tp1_price to zone (ArmedZone is frozen — use dc_replace)
        if tp1_price is not None:
            zone = dc_replace(zone, tp1_price=tp1_price)
            self.armed_tracker._active = zone  # update tracker's active zone

        self._pending_signal = dc_replace(signal, armed_zone=zone)
        log.info(
            "Signal armed: %s %s zone=[%s-%s] entry=%s mode=%s tp1=%s",
            signal.killzone, signal.side,
            signal.fvg_low, signal.fvg_high,
            zone.entry_price, mode, tp1_price,
        )
        return None  # wait for armed zone to fill

    def try_signal_from_forming(self, forming_bar: Bar) -> Optional[Signal]:
        """
        Check if the displacement candidate (b2 = window[-1]) can invert a
        prior active FVG. peek_displacement already gates on this — if it
        returns non-None, a prior FVG is invertible. The forming bar (b3)
        is no longer used to generate the FVG; the entry FVG is the prior
        inverted one identified by _find_inverted_fvg(b2, side).
        Returns None if no pending sweep, no displacement, or no prior FVG.
        """
        peek = self.displacement.peek_displacement()
        if peek is None:
            return None
        side, b1, b2 = peek

        ifvg = self.displacement._find_inverted_fvg(b2, side, prev_close=b1.close)
        if ifvg is None:
            return None

        body = abs(b2.close - b2.open)
        atr = self.displacement.atr or body
        event = DisplacementEvent(
            side=side,
            displacement_bar=b2,
            body_size=body,
            atr_at_event=atr,
            body_to_atr=body / atr,
            fvg=ifvg,
        )
        candidate = self.composer.on_displacement(forming_bar, event)
        if candidate is None:
            return None
        grade = self.grader.score(
            candidate, event, self.displacement.active_fvgs,
            min_grade=self.strategy_cfg.grader_min_grade,
            gapping_sack_enabled=self.strategy_cfg.ifvg_gapping_sack_enabled,
        )
        if not grade.passes:
            log.info("Forming-bar signal filtered: %s — %s", grade.grade, grade.reason)
            return None
        graded = dc_replace(candidate, setup_grade=grade)
        return self._arm_or_return(forming_bar, graded, event)


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
        on_pre_place: PrePlaceCallback | None = None,
        on_reject: "Callable[[RejectInfo, str], Awaitable[None]] | None" = None,
        replay_mode: bool = False,
        contracts: int = 1,
        risk_per_trade_pct: Decimal = Decimal("0"),
        strategy_cfg: "StrategyParams | None" = None,
        commission_per_contract: Decimal = Decimal("0"),
        max_contracts_override: int | None = None,
        forming_bar_entries: bool = False,
        flatten_enabled: bool = True,
        flatten_time_ct: str = "15:05",
        entry_cutoff_time_ct: str = "14:30",
    ) -> None:
        self.broker = broker
        self.risk_state = risk_state
        self.runners = {r.instrument: r for r in runners}
        # Maps signal_instrument → execution instrument when they differ (GC → MGC).
        # Built from runners that have a non-empty signal_instrument.
        self._bar_router: dict[str, str] = {
            r.signal_instrument: r.instrument
            for r in runners
            if r.signal_instrument and r.signal_instrument != r.instrument
        }
        self.on_signal = on_signal
        self.contracts = contracts  # contracts per signal; hot-applied via PATCH /api/config
        self.risk_per_trade_pct = risk_per_trade_pct  # 0 = use fixed contracts; else % equity risked; hot-applied
        self.strategy_cfg = strategy_cfg
        self.commission_per_contract = commission_per_contract  # deducted per fill side; hot-applied
        self.max_contracts_override = max_contracts_override  # None = use account-level cap; hot-applied
        # Mid-bar entries: when False (default), the b3 confirmation must come
        # from a CLOSED bar — the only path the backtest validates. The poll
        # task always runs; it checks this flag per tick so PATCH hot-applies.
        self.forming_bar_entries = forming_bar_entries
        self.flatten_enabled = flatten_enabled            # hot-applied via PATCH /api/config
        self.flatten_time_ct = flatten_time_ct
        self.entry_cutoff_time_ct = entry_cutoff_time_ct
        self._flatten_task: asyncio.Task | None = None
        self._flattened_today: str | None = None  # trading-day key, avoid re-flatten spam
        # Called immediately after broker.place_bracket() succeeds so the
        # reconciler can start its fill-latency grace window.
        self._on_order_placed = on_order_placed
        # Called BEFORE await broker.place_bracket() so signal meta is written
        # before the market-order fill can race in during the HTTP round-trip.
        self._on_pre_place = on_pre_place
        # Called when the runner rejects a setup internally (grader-B, premature
        # liquidity, invalidate) so the rejection ledger can record the miss.
        self._on_reject = on_reject

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

        # Forming bar polling — records the ts of b2 (displacement bar) for
        # which we already fired a mid-bar signal, per instrument.
        self._forming_signal_fired: dict[str, datetime] = {}
        self._poll_task: "asyncio.Task | None" = None

        # When a signal is opposite to the current open position, we flatten
        # first and store the signal here. Executed once the flatten fill
        # confirms we're flat (open_contracts == 0).
        self._pending_reversal: dict[str, Signal] = {}

        # Instruments for which _flatten_for_reversal has been deliberately
        # initiated. _handle_fill only triggers _execute_reversal when the
        # instrument is in this set — preventing natural stop/target fills from
        # triggering a stale pending reversal.
        self._reversal_flatten_active: set[str] = set()

        self._started = False

        # Rule F: premature-liquidity cancel.
        # Maps instrument → (tp1_price, side) for the most recently placed
        # pending entry. Cleared on fill. When a bar crosses the TP1 level
        # before the entry fills, the pending order is cancelled — the setup
        # is dead because the opposing liquidity has already been taken.
        self._pending_entry_tp1: dict[str, tuple[Decimal, str]] = {}

        # HTF confluence trackers — set externally by main.py after warm-up.
        # Engine-owned (not per-runner) so they survive /api/strategy/reload.
        # None until wired; the on_bar gates no-op while None or while the
        # corresponding strategy_cfg flag is off.
        self.htf_bias = None      # HTFBiasTracker | None
        self.htf_levels = None    # HTFLevelFinder | None
        # Which instrument's bars htf_levels was built from. Only apply to matching signals.
        self._htf_instrument: str | None = None
        # One-shot warning guard: fires once if a flag is on but the tracker is
        # None (e.g. rebuild failed). Resets when trackers are successfully wired.
        self._htf_warned: bool = False

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
        if not self._replay_mode:
            self._poll_task = asyncio.create_task(self._poll_forming_bars())
            if self._flatten_task is None:
                self._flatten_task = asyncio.create_task(self._flatten_clock())
        log.info(
            "ExecutionEngine started: %d instruments tracked, forming_bar_entries=%s",
            len(self.runners), self.forming_bar_entries,
        )

    async def stop(self) -> None:
        """Graceful shutdown: cancel all working orders, do not flatten."""
        if not self._started:
            return
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if self._flatten_task is not None:
            self._flatten_task.cancel()
            try:
                await self._flatten_task
            except asyncio.CancelledError:
                pass
            self._flatten_task = None
        # Cancel only — we do NOT flatten on stop. The operator may be
        # restarting the bot mid-position; auto-flattening would be
        # surprising. The kill-switch endpoint is for that.
        try:
            await self.broker.cancel_all()
        except Exception as e:
            log.warning("cancel_all on stop failed: %s", e)
        self._started = False

    # ------------------------------------------------------------------
    # Flatten-rule enforcement
    # ------------------------------------------------------------------

    async def _enforce_flatten(self, ts: datetime) -> None:
        """Topstep flatten rule: flat by 3:10 PM CT — we act at flatten_time_ct.

        Bar-driven so it works identically in replay and live; live also has
        a wall-clock task because 5min bars can arrive late.
        """
        if not self.flatten_enabled:
            return
        if not in_flatten_window(ts, self.flatten_time_ct):
            return
        day_key = ts.astimezone(timezone.utc).date().isoformat()
        if self._flattened_today == day_key:
            return
        open_insts = self._open_instruments()
        if not open_insts:
            self._flattened_today = day_key
            return
        log.warning("FLATTEN WINDOW: closing all positions at %s (rule: flat by 3:10 PM CT)", ts)
        for inst in open_insts:
            try:
                await self.broker.cancel_all(inst)
                await self.broker.flatten(inst)
            except Exception:
                log.exception("flatten-window close failed for %s", inst)
        self._flattened_today = day_key

    def _open_instruments(self) -> list[str]:
        """Instruments with open positions, per broker truth where available."""
        if callable(getattr(self.broker, "open_brackets", None)):
            return sorted({b["instrument"] for b in self.broker.open_brackets()})
        if self.risk_state.open_contracts != 0:
            return [r.instrument for r in self.runners.values()]
        return []

    async def _flatten_clock(self) -> None:
        """Live backup for bar-driven flatten: check every 30s of wall time."""
        while True:
            await asyncio.sleep(30)
            try:
                await self._enforce_flatten(datetime.now(timezone.utc))
            except Exception:
                log.exception("flatten clock check failed")

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
        await self._enforce_flatten(bar.ts)
        # Resolve: a GC bar routes to the MGC runner via _bar_router.
        execution_key = self._bar_router.get(bar.instrument, bar.instrument)

        # Rule F: premature-liquidity cancel.
        # If we have a pending limit entry and the bar crossed the TP1 price
        # before the entry filled, the setup's opposing liquidity is already
        # taken — cancel all working orders for this instrument.
        # Runs before the runner-None guard so it fires even during misconfiguration.
        pending_tp1 = self._pending_entry_tp1.get(execution_key)
        if pending_tp1 is not None and self.risk_state.open_contracts == 0:
            tp1_lvl, tp1_side = pending_tp1
            hit = (
                (tp1_side == "long" and bar.high >= tp1_lvl)
                or (tp1_side == "short" and bar.low <= tp1_lvl)
            )
            if hit:
                log.info(
                    "Premature liquidity: TP1 %s hit before %s entry filled — cancelling pending orders",
                    tp1_lvl, tp1_side,
                )
                self._pending_entry_tp1.pop(execution_key, None)
                asyncio.create_task(self.broker.cancel_all(execution_key))

        runner = self.runners.get(execution_key)
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

        if signal is None:
            # Runner rejected internally (grader-B / premature-liq / invalidate).
            # Skip stale/warmup bars so the ledger holds live misses only.
            rej = runner.last_reject
            if rej is not None and self._on_reject is not None and not is_stale:
                try:
                    await self._on_reject(rej, runner.instrument)
                except Exception:
                    log.exception("on_reject callback raised")
            return

        # Armed-zone fills are live market events — price re-entered the zone on
        # THIS bar — so the warmup/replay staleness guard must not discard them.
        # Staleness still applies to fresh signals so we don't trade on bars that
        # a reconnect re-delivered with old timestamps. Both branches log at
        # WARNING: the prior DEBUG line made silent drops invisible at INFO and
        # swallowed legitimate armed fills during SignalR reconnect churn.
        if is_stale:
            age_secs = (datetime.now(timezone.utc) - bar.ts).total_seconds()
            if signal.armed_zone is None:
                log.warning(
                    "Stale bar %s (age=%.0fs) produced a signal — skipping order "
                    "(bar-stream latency / reconnect churn).",
                    bar.ts, age_secs,
                )
                return
            log.warning(
                "Armed-zone fill on stale bar %s (age=%.0fs) — placing anyway; "
                "armed fills are live events.",
                bar.ts, age_secs,
            )

        original_signal = signal
        signal, deny_reason = self._apply_confluence(signal, runner)
        if deny_reason is not None:
            denied = OrderOutcome(placed=False, reason=deny_reason)
            if self.on_signal is not None:
                try:
                    await self.on_signal(original_signal, denied)
                except Exception:
                    log.exception("on_signal callback raised (%s)", deny_reason)
            return

        outcome = await self._act_on_signal(signal)
        if self.on_signal is not None:
            # Best-effort: a journaling failure shouldn't stop trading.
            try:
                await self.on_signal(signal, outcome)
            except Exception:
                log.exception("on_signal callback raised")

    async def _handle_fill(self, fill: Fill) -> None:
        """Fill arrived. Update risk state. Synchronous, no await needed."""
        if fill.is_entry:
            # Entry confirmed — TP1 premature-liquidity watch is no longer needed.
            self._pending_entry_tp1.pop(fill.instrument, None)
        commission = self.commission_per_contract * fill.size if self.commission_per_contract else Decimal("0")
        self.risk_state.record_fill(
            realized_pnl_delta=fill.realized_pnl_delta - commission,
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
        # Notify the matching runner's composer when a stop fill lands.
        if not fill.is_entry and fill.is_stop:
            runner = self.runners.get(fill.instrument)
            if runner is not None:
                runner.composer.on_stop_loss()

        # Only trigger reversal when we deliberately initiated a flatten for
        # reversal purposes. Natural stop/target fills that happen to bring
        # open_contracts to 0 must NOT consume a stale _pending_reversal.
        if self.risk_state.open_contracts == 0 and fill.instrument in self._reversal_flatten_active:
            self._reversal_flatten_active.discard(fill.instrument)
            pending = self._pending_reversal.pop(fill.instrument, None)
            if pending is not None:
                log.info(
                    "Flat after reversal flatten — entering: %s", pending.rationale,
                )
                asyncio.create_task(self._execute_reversal(pending))

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

    def _apply_confluence(
        self, signal: "Signal", runner: "StrategyRunner",
    ) -> "tuple[Signal | None, str | None]":
        """Run HTF bias gate + VP filter + HTF/VP target precedence on a signal.

        Shared by both live entry paths (closed-bar on_bar and the forming-bar
        poller) so they gate and re-target identically.

        Returns (signal, None) if the signal passes — `signal` may carry a new
        target. Returns (None, reason) if a gate blocks it, where reason is
        "htf_bias" or "vp_filter". Pure decision: does not call on_signal.
        """
        # Engine-level strategy_cfg on purpose: VP/HTF confluence toggles are
        # hot-applied here by PATCH /api/config without a runner rebuild.
        # Per-instrument overrides live on runner.strategy_cfg (runner-level
        # fields only — composer/displacement/liquidity/grader/iFVG).
        cfg = self.strategy_cfg
        if cfg is not None and not self._htf_warned:
            if (cfg.htf_bias_enabled and self.htf_bias is None) or (
                cfg.htf_target_enabled and self.htf_levels is None
            ):
                log.warning(
                    "HTF flag enabled but tracker is None — gate/target inactive "
                    "until trackers rebuild (bias_enabled=%s bias=%s, "
                    "target_enabled=%s levels=%s)",
                    cfg.htf_bias_enabled, self.htf_bias is not None,
                    cfg.htf_target_enabled, self.htf_levels is not None,
                )
                self._htf_warned = True
        vp_active = (
            runner.vp is not None
            and cfg is not None
            and cfg.vp_enabled
            and runner.vp.has_prior_profile()
        )

        # Part A — HTF bias gate. Block counter-trend signals; remember when
        # the bias AGREES (used to bypass the VP value-area filter below).
        bias_agrees = False
        if cfg is not None and cfg.htf_bias_enabled and self.htf_bias is not None:
            b = self.htf_bias.bias()
            if (b == "bullish" and signal.side == "short") or (
                b == "bearish" and signal.side == "long"
            ):
                log.info(
                    "HTF bias gate: %s signal blocked (4h bias=%s) | %s",
                    signal.side, b, signal.rationale,
                )
                return None, "htf_bias"
            bias_agrees = (b == "bullish" and signal.side == "long") or (
                b == "bearish" and signal.side == "short"
            )

        # VP value-area filter — skipped when an enabled, agreeing 4h bias
        # vouches for the direction (the 2026-05-27 below-value-area shorts).
        if vp_active and not bias_agrees:
            if not runner.vp.passes_filter(signal, self.strategy_cfg):
                log.info(
                    "VP filter: rejected %s entry=%.2f outside value area | %s",
                    signal.side, float(signal.entry), signal.rationale,
                )
                return None, "vp_filter"

        # Part B — target precedence: HTF (4h FVG → 30min swing) wins,
        # VP target is the fallback, fixed r_multiple is the final fallback.
        target_chosen = False
        if (
            cfg is not None and cfg.htf_target_enabled and self.htf_levels is not None
            and (self._htf_instrument is None or signal.instrument == self._htf_instrument)
        ):
            found = self.htf_levels.find_target(
                signal.side, signal.entry, signal.stop, cfg.htf_target_min_r,
            )
            if found is not None:
                price, label = found
                signal = dataclasses.replace(
                    signal, target=price, rationale=signal.rationale + f" | {label}",
                )
                target_chosen = True
        if not target_chosen and vp_active:
            vp_signal = runner.vp.select_target(signal, self.strategy_cfg)
            if vp_signal is not None:
                signal = vp_signal

        return signal, None

    def _entry_size(self, signal: Signal) -> int:
        """Contracts for this entry. Risk-based when risk_per_trade_pct > 0,
        else the fixed `contracts` count. Logs the decision (Rule 12)."""
        if not self.risk_per_trade_pct or self.risk_per_trade_pct <= 0:
            return self.contracts

        equity = self.risk_state.current_equity
        if equity <= 0:  # before the first mark-to-market tick of the session
            equity = self.risk_state.realized_balance
        offset = self.risk_state.config.risk_sizing_equity_offset
        if offset > 0:
            equity = max(equity - offset, Decimal("1"))
        stop_distance = abs(signal.entry - signal.stop)
        if stop_distance <= 0:
            return self.contracts  # degenerate signal; fall back rather than divide by zero

        pv = _point_value(signal.instrument)
        account_max = self.risk_state.config.max_contracts
        effective_max = (
            min(account_max, self.max_contracts_override)
            if self.max_contracts_override is not None
            else account_max
        )
        size = risk_based_size(
            equity, self.risk_per_trade_pct, stop_distance, pv,
            max_size=effective_max,
        )
        budget = equity * (self.risk_per_trade_pct / Decimal("100"))
        risk_per_contract = stop_distance * pv
        over = " (OVER-BUDGET floored to 1)" if risk_per_contract > budget else ""
        offset_note = f" (profit-above-base; offset={offset})" if offset > 0 else ""
        log.info(
            "Risk-sized: equity=%s budget=%s stop=%spt $/ct=%s -> size=%d%s%s",
            equity, budget, stop_distance, risk_per_contract, size, over, offset_note,
        )
        return size

    async def _act_on_signal(self, signal: Signal) -> OrderOutcome:
        """Run the pretrade gate and place if allowed. Caller holds the lock."""
        # Opposite-side signal while holding a position: flatten first, then
        # reverse. This MUST be checked before the pretrade gate. The gate only
        # denies (MAX_CONTRACTS) when headroom is exhausted, but max_contracts
        # (30) far exceeds the traded size, so the gate would Allow the opposing
        # entry and stack a second, conflicting bracket on a netted position
        # (2026-06-07 incident: long 5 + short 6 -> tangled net -1, orphan left
        # open). Routing here restores the intended flatten-before-reverse flow.
        if (
            self.risk_state.open_contracts != 0
            and _is_opposite_side(signal.side, self.risk_state.open_contracts)
            and signal.instrument not in self._reversal_flatten_active
        ):
            log.info(
                "Opposite-side signal while in position — flattening for reversal: %s",
                signal.rationale,
            )
            self._pending_reversal[signal.instrument] = signal
            asyncio.create_task(self._flatten_for_reversal(signal.instrument))
            return OrderOutcome(placed=False, reason="reversal_pending")

        if self.flatten_enabled and past_entry_cutoff(signal.created_at, self.entry_cutoff_time_ct):
            log.info("Entry blocked: past %s CT entry cutoff (flatten rule)", self.entry_cutoff_time_ct)
            return OrderOutcome(placed=False, reason="entry_cutoff")

        order = ProposedOrder(
            instrument=signal.instrument,
            side=signal.side,
            size=self._entry_size(signal),
            entry=signal.entry,
            stop=signal.stop,
            target=signal.target,
            is_entry=True,
        )
        decision = check(order, self.risk_state)

        if isinstance(decision, Deny):
            if (
                decision.reason_code == "MAX_CONTRACTS"
                and _is_opposite_side(signal.side, self.risk_state.open_contracts)
            ):
                log.info(
                    "Opposite-side signal while in position — flattening for reversal: %s",
                    signal.rationale,
                )
                self._pending_reversal[signal.instrument] = signal
                asyncio.create_task(self._flatten_for_reversal(signal.instrument))
                return OrderOutcome(placed=False, reason="reversal_pending")
            log.info(
                "Signal denied: %s — %s (%s)",
                signal.rationale, decision.reason_code, decision.message,
            )
            return OrderOutcome(placed=False, reason=decision.reason_code)

        # Feed health gate: without a live WebSocket feed, fill events never
        # arrive → open_contracts stays 0 → reconciler drift fires on every
        # entry. Block orders until the feed reconnects.
        if not self.broker.feed_is_healthy():
            log.warning(
                "Signal blocked: real-time feed disconnected — "
                "entry would cause reconcile drift (%s @ %s)",
                signal.side, signal.entry,
            )
            return OrderOutcome(placed=False, reason="feed_disconnected")

        # Prime the reconciler grace window BEFORE sending to exchange.
        # Market orders fill in microseconds; the reconciler can tick during
        # the HTTP round-trip and see broker=N, internal=0 before we call
        # notify_order_placed() — triggering a false-positive drift. Starting
        # the grace window here ensures it is active before the order hits the
        # exchange. If the broker rejects, the grace runs harmlessly (no
        # position opened, so no contract drift to alarm on).
        if self._on_order_placed is not None:
            self._on_order_placed()

        # Write signal meta before the HTTP call so it's available if the
        # market-order fill arrives via WebSocket during the await below.
        if self._on_pre_place is not None:
            try:
                await self._on_pre_place(signal, decision.allowed_size)
            except Exception:
                log.exception("on_pre_place callback raised")

        # Compute structural TP1 from the nearest HTF swing in trade direction.
        # Uses the runner's grader swing data — already computed during on_bar.
        # Falls back gracefully to None (uses partial_profit_r path instead).
        tp1_price: Decimal | None = None
        tp1_fraction = Decimal("0.5")
        be_after_tp1 = True
        runner = self.runners.get(signal.instrument)
        if runner is not None and self.strategy_cfg is not None:
            highs = runner.grader._htf_swing_highs
            lows = runner.grader._htf_swing_lows
            if signal.side == "long":
                candidates = [h for h in highs if h > signal.entry]
                tp1_price = min(candidates) if candidates else None
            else:
                candidates = [lo for lo in lows if lo < signal.entry]
                tp1_price = max(candidates) if candidates else None
            tp1_fraction = self.strategy_cfg.ifvg_tp1_fraction
            be_after_tp1 = self.strategy_cfg.ifvg_be_after_tp1

        # Allowed — place the bracket. Note: the gate may have sized down,
        # which is reflected in decision.allowed_size.
        result = await self.broker.place_bracket(
            instrument=signal.instrument,
            side=signal.side,
            size=decision.allowed_size,
            entry=signal.entry,
            stop=signal.stop,
            target=signal.target,
            tp1_price=tp1_price,
            tp1_fraction=tp1_fraction if tp1_price is not None else Decimal("0.5"),
            be_after_tp1=be_after_tp1 if tp1_price is not None else True,
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
            "Bracket placed: %s size=%d entry=%s stop=%s target=%s tp1=%s",
            signal.rationale,
            decision.allowed_size,
            signal.entry, signal.stop, signal.target, tp1_price,
        )

        # Rule F: arm the premature-liquidity watch.
        # Only for limit entries — market entries fill immediately so there is
        # no pending window during which TP1 can be hit first.
        broker_entry_mode = getattr(self.broker, "entry_mode", "market")
        if broker_entry_mode == "limit" and tp1_price is not None:
            self._pending_entry_tp1[signal.instrument] = (tp1_price, signal.side)

        return OrderOutcome(
            placed=True,
            reason="allowed",
            allowed_size=decision.allowed_size,
            broker_order_id=result.entry_order_id,
        )

    async def _poll_forming_bars(self) -> None:
        """
        Background task: poll every 1s for the current forming bar.
        If the forming bar already satisfies the FVG condition for the last
        displacement, fire a signal immediately (market entry mid-bar).
        Deduped per displacement bar so we only fire once per setup.
        """
        while True:
            await asyncio.sleep(1)
            if not self.forming_bar_entries:
                continue
            for instrument, runner in self.runners.items():
                try:
                    get_fb = getattr(self.broker, "get_forming_bar", None)
                    if get_fb is None:
                        continue
                    forming_bar = await get_fb(runner.timeframe)
                    if forming_bar is None:
                        continue

                    peek = runner.displacement.peek_displacement()
                    if peek is None:
                        continue
                    side, _b1, b2 = peek
                    if self._forming_signal_fired.get(instrument) == b2.ts:
                        continue

                    # Pending sweep + displacement candidate — check FVG
                    awaiting_count = len(runner.composer.awaiting)
                    log.debug(
                        "Forming bar poll: %s %s disp_bar=%s forming_close=%s awaiting=%d",
                        instrument, side, b2.ts.strftime("%H:%M"),
                        forming_bar.close, awaiting_count,
                    )

                    signal = runner.try_signal_from_forming(forming_bar)
                    if signal is None:
                        continue

                    self._forming_signal_fired[instrument] = b2.ts

                    original_signal = signal
                    signal, deny_reason = self._apply_confluence(signal, runner)
                    if deny_reason is not None:
                        denied = OrderOutcome(placed=False, reason=deny_reason)
                        if self.on_signal is not None:
                            try:
                                await self.on_signal(original_signal, denied)
                            except Exception:
                                log.exception("on_signal callback raised (forming bar %s)", deny_reason)
                        continue

                    log.info("Forming bar signal: %s", signal.rationale)
                    outcome = await self._act_on_signal(signal)
                    if self.on_signal is not None:
                        try:
                            await self.on_signal(signal, outcome)
                        except Exception:
                            log.exception("on_signal callback raised (forming bar)")
                except Exception:
                    log.exception("_poll_forming_bars failed for %s", instrument)

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

    async def _flatten_for_reversal(self, instrument: str) -> None:
        """Cancel open brackets and flatten the position before a reversal entry."""
        self._reversal_flatten_active.add(instrument)
        try:
            await self.broker.cancel_all(instrument)
        except Exception:
            log.exception("cancel_all during reversal failed for %s", instrument)
        try:
            await self.broker.flatten(instrument)
        except Exception:
            log.exception("flatten during reversal failed for %s — dropping pending reversal", instrument)
            self._reversal_flatten_active.discard(instrument)
            self._pending_reversal.pop(instrument, None)
            return

        # Edge case: position was already flat when flatten() was called (e.g.
        # the position closed naturally just before the reversal flatten ran).
        # No fill event will arrive, so _handle_fill never fires — execute the
        # reversal directly here instead.
        if self.risk_state.open_contracts == 0 and instrument in self._reversal_flatten_active:
            self._reversal_flatten_active.discard(instrument)
            pending = self._pending_reversal.pop(instrument, None)
            if pending is not None:
                log.info(
                    "Already flat when reversal flatten ran — entering immediately: %s",
                    pending.rationale,
                )
                asyncio.create_task(self._execute_reversal(pending))

    async def _execute_reversal(self, signal: Signal) -> None:
        """Place the deferred reversal entry after the flatten fill confirmed flat."""
        outcome = await self._act_on_signal(signal)
        if self.on_signal is not None:
            try:
                await self.on_signal(signal, outcome)
            except Exception:
                log.exception("on_signal callback raised (reversal)")
