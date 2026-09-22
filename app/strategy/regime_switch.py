"""
RegimeSwitchRunner â€” one engine per day, selected by a daily range regime.

Lawrence's hypothesis (2026-06-12): ORB earns in iFVG's quiet months and
vice versa, but a shared account budget couples them (the combined-engine
failure). Route by regime instead: on SMALL-range days the quiet engine
(ORB) trades; on LARGE-range days the active engine (iFVG) trades. Only
one engine spends the loss budget on any given day.

Regime metric (parameter-free by design â€” no new tunables): the previous
ET-day's (highâˆ’low)/close vs the trailing 60-day median of the same.
Below median â†’ quiet day â†’ ORB. At/above â†’ iFVG. Decided once per day at
the ET date roll; during warmup (<10 days of history) the active (iFVG)
side trades, matching the deployed status quo.

Both sub-runners receive EVERY bar (detector state must stay warm); only
signal emission is gated. Exit requests pass through from either side
unconditionally â€” a position must always be exitable.
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import date
from decimal import Decimal
from statistics import median
from typing import Optional
from zoneinfo import ZoneInfo

from app.sim.events import Bar
from app.strategy.composer import Signal

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

_LOOKBACK_DAYS = 60
_WARMUP_DAYS = 10


class RegimeSwitchRunner:
    def __init__(self, quiet, active, delegate=None) -> None:
        self.quiet = quiet        # trades small-range (below-median) days
        self.active = active      # trades large-range days; warmup default
        # Engine-facing attribute delegation target. Must be the iFVG runner
        # regardless of routing polarity â€” run_backtest feeds HTF/delivery
        # FVGs through runner.grader, which only iFVG consumes.
        self._delegate = delegate if delegate is not None else active
        self._day: date | None = None
        self._day_high: Decimal | None = None
        self._day_low: Decimal | None = None
        self._day_close: Decimal | None = None
        self._range_hist: deque[float] = deque(maxlen=_LOOKBACK_DAYS)
        self._selected = "active"
        self.exit_request: str | None = None

    @property
    def selected(self) -> str:
        return self._selected

    def _roll_day(self, bar: Bar) -> None:
        d = bar.ts.astimezone(ET).date()
        if d == self._day:
            return
        if self._day is not None and self._day_close:
            rng_pct = float((self._day_high - self._day_low) / self._day_close)
            prev = self._selected
            if len(self._range_hist) >= _WARMUP_DAYS:
                self._selected = ("quiet" if rng_pct < median(self._range_hist)
                                  else "active")
            else:
                self._selected = "active"
            self._range_hist.append(rng_pct)
            if self._selected != prev:
                log.info("Regime switch: %s engine active for %s "
                         "(prev-day range %.3f%%)", self._selected, d,
                         rng_pct * 100)
        self._day = d
        self._day_high = self._day_low = self._day_close = None

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        self._roll_day(bar)
        self._day_high = bar.high if self._day_high is None else max(self._day_high, bar.high)
        self._day_low = bar.low if self._day_low is None else min(self._day_low, bar.low)
        self._day_close = bar.close

        sig_q = self.quiet.on_bar(bar)
        sig_a = self.active.on_bar(bar)

        # Exit requests are honored from EITHER side, gated or not.
        for r in (self.quiet, self.active):
            req = getattr(r, "exit_request", None)
            if req is not None:
                r.exit_request = None
                self.exit_request = req

        return sig_q if self._selected == "quiet" else sig_a

    # Engine-facing surface delegates to the active (iFVG) runner.
    @property
    def instrument(self):
        return self._delegate.instrument

    @property
    def timeframe(self):
        return self._delegate.timeframe

    @property
    def strategy_cfg(self):
        return self._delegate.strategy_cfg

    @property
    def vp(self):
        return self._delegate.vp

    @property
    def composer(self):
        return self._delegate.composer

    @property
    def grader(self):
        return self._delegate.grader

    @property
    def signal_instrument(self):
        return self._delegate.signal_instrument

    @property
    def last_reject(self):
        return self._delegate.last_reject
