"""
CombinedRunner — two signal engines, one runner interface.

Feeds every bar to both sub-runners (iFVG primary, ORB secondary) and
emits at most one Signal per bar; the primary wins the rare same-bar
collision (a graded setup beats a clock breakout). All engine-facing
attributes delegate to the primary, so ExecutionEngine, server.py and
main.py need no changes — the engine sees a single runner per
instrument. Signals are source-agnostic downstream: an ORB signal
opposite an open iFVG position triggers the normal reversal flow.

B47: DailySessionContext is a shared state object injected into both
sub-runners when ifvg_orb_confluence_gate=True. It tracks today's iFVG
signal sides and the ORB direction, enabling cross-engine directional
gating without coupling the runner implementations to each other.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from app.broker.events import Bar
from app.strategy.composer import Signal

log = logging.getLogger(__name__)


class DailySessionContext:
    """
    B47: shared cross-engine state for the iFVG×ORB confluence gate.

    Records iFVG signal sides and the ORB direction for the current ET day.
    Resets automatically when the ET date changes. Thread-safety not required
    (single-threaded bar loop).

    Gate 1 — ORBDetector: suppress when all prior same-day iFVG are OPPOSITE.
    Gate 2 — SweepDisplacementComposer: suppress post-ORB iFVG that OPPOSES ORB.
    """

    def __init__(self) -> None:
        self._et_date: date | None = None
        self._ifvg_sides: list[str] = []
        self._orb_direction: str | None = None

    def _reset_if_new_day(self, et_date: date) -> None:
        if et_date != self._et_date:
            self._et_date = et_date
            self._ifvg_sides = []
            self._orb_direction = None

    def record_ifvg_signal(self, et_date: date, side: str) -> None:
        """Called by iFVG composer after a signal is emitted."""
        self._reset_if_new_day(et_date)
        self._ifvg_sides.append(side)

    def record_orb_direction(self, et_date: date, side: str) -> None:
        """Called by ORB detector after a signal is emitted."""
        self._reset_if_new_day(et_date)
        self._orb_direction = side

    def gate1_orb_suppressed(self, et_date: date, orb_side: str) -> bool:
        """Return True if ORB should be suppressed (all prior iFVG today oppose ORB)."""
        self._reset_if_new_day(et_date)
        if not self._ifvg_sides:
            return False  # no prior iFVG today → allow
        opp = "short" if orb_side == "long" else "long"
        return all(s == opp for s in self._ifvg_sides)

    def gate2_ifvg_suppressed(self, et_date: date, ifvg_side: str) -> bool:
        """Return True if post-ORB iFVG should be suppressed (opposes ORB direction)."""
        self._reset_if_new_day(et_date)
        if self._orb_direction is None:
            return False  # no ORB fired today → allow
        return ifvg_side != self._orb_direction

    def gate_b56_orb_suppressed(self, et_date: date, orb_side: str) -> bool:
        """B56: Return True if ORB should be suppressed (no prior same-direction iFVG today).

        Groups suppressed: B (no prior iFVG, PF=0.990) and C (all opposite, PF=0.939).
        Groups allowed:    A (any same-dir iFVG, PF=1.707) and D (mixed, PF=1.224).
        """
        self._reset_if_new_day(et_date)
        return not any(s == orb_side for s in self._ifvg_sides)


class CombinedRunner:
    def __init__(self, primary, secondary, confluence_gate: bool = False,
                 alignment_gate: bool = False) -> None:
        self.primary = primary
        self.secondary = secondary
        # B47: inject shared cross-engine session context when confluence gate is enabled.
        if confluence_gate:
            ctx = DailySessionContext()
            if hasattr(primary, "composer"):
                primary.composer.session_ctx = ctx
            if hasattr(secondary, "detector"):
                secondary.detector.session_ctx = ctx
        # B56: inject alignment context when alignment gate is enabled.
        if alignment_gate:
            actx = DailySessionContext()
            if hasattr(primary, "composer"):
                primary.composer.alignment_ctx = actx
            if hasattr(secondary, "detector"):
                secondary.detector.alignment_ctx = actx

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        sig_p = self.primary.on_bar(bar)
        sig_s = self.secondary.on_bar(bar)
        if sig_p is not None and sig_s is not None:
            log.info("Engine collision on %s: iFVG and ORB both fired — iFVG wins",
                     bar.ts)
            return sig_p
        return sig_p if sig_p is not None else sig_s

    # Engine-facing surface delegates to the primary (iFVG) runner.
    @property
    def instrument(self):
        return self.primary.instrument

    @property
    def timeframe(self):
        return self.primary.timeframe

    @property
    def strategy_cfg(self):
        return self.primary.strategy_cfg

    @property
    def vp(self):
        return self.primary.vp

    @property
    def composer(self):
        return self.primary.composer

    @property
    def displacement(self):
        # server.py's strategy_state SSE and /api/forming/status read
        # runner.displacement (peek + atr) — must exist for live deployment.
        return self.primary.displacement

    @property
    def grader(self):
        return self.primary.grader

    @property
    def signal_instrument(self):
        return self.primary.signal_instrument

    @property
    def last_reject(self):
        return self.primary.last_reject
