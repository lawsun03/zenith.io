"""
CombinedRunner — two signal engines, one runner interface.

Feeds every bar to both sub-runners (iFVG primary, ORB secondary) and
emits at most one Signal per bar; the primary wins the rare same-bar
collision (a graded setup beats a clock breakout). All engine-facing
attributes delegate to the primary, so ExecutionEngine, server.py and
main.py need no changes — the engine sees a single runner per
instrument. Signals are source-agnostic downstream: an ORB signal
opposite an open iFVG position triggers the normal reversal flow.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.broker.events import Bar
from app.strategy.composer import Signal

log = logging.getLogger(__name__)


class CombinedRunner:
    def __init__(self, primary, secondary) -> None:
        self.primary = primary
        self.secondary = secondary

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
