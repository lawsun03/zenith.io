"""
news_straddle — CPI breakout-straddle detector (B89, confirmed in B85).

Mechanism (validated on 1-second data, B85: PF ~5-6, 5/5 years positive):
  - For each scheduled news release, lock the HIGH/LOW of the 15-min PRE-release
    range (no lookahead — the bar timestamped AT the release is the first ENTRY
    bar, not part of the range).
  - Arm an OCO stop straddle: buy_stop = range_high + offset, sell_stop =
    range_low - offset (offset = offset_ticks × tick; default 60 ticks).
  - The first leg to be tagged fires a Signal; the sibling is cancelled (OCO,
    one trade per event). Stop = the broken range boundary (R = offset, a TIGHT
    stop). Target = tp_r × R (default 3R; 4R also a headline config).
  - Both legs tagged in ONE bar = whipsaw: no tradeable signal (on a real
    exchange the filled leg is immediately stopped by the sibling = -1R; the
    oracle counts that loss, the engine signal-stream omits it — see below).
  - Flat by max_hold (mirrors the oracle's 180-min cap) via the exit_request
    channel; the bot also flattens EOD globally.

FILL-MODEL NOTE (Rule 7 / Rule 12 — load-bearing): the entry here is a RESTING
STOP order that fills AT the stop level. PaperBroker fills entries at market
(last bar close), not at signal.entry (the stale-FVG fix). So run_backtest
CANNOT faithfully price this strategy — its P&L is established by the oracle
(scripts/news_straddle_cpi_1s.py, B85), and this engine reproduces the oracle's
SIGNALS. The live path (follow-on) places the OCO as real exchange stop entries
and reuses _pending_brackets on fill; this detector computes the range/levels.

Closed-bar lesson: that lesson governs momentum-confirmation signals keying off
incomplete bars. A resting stop order fills deterministically on touch, so
intrabar break detection (bar.high ≥ buy / bar.low ≤ sell) is the faithful model
and matches the oracle. Faithful at 1s granularity; live uses resting orders.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from app.bot_config import StrategyParams
from app.broker.events import Bar
from app.strategy.composer import Signal
from app.strategy.grader import SetupGrader

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# Per-event lifecycle status.
_PENDING = "pending"    # release in the future, range not yet locked
_ARMED = "armed"        # range locked, OCO live, watching the entry window
_FIRED = "fired"        # a leg filled; sibling cancelled (terminal)
_WHIPSAW = "whipsaw"    # both legs tagged in one bar (terminal)
_NOFILL = "nofill"      # entry window elapsed with no break (terminal)
_SKIPPED = "skipped"    # insufficient pre-range coverage (terminal)


def load_event_times(path: str, event_type: str) -> list[datetime]:
    """Read release timestamps (UTC-aware) of one event_type from news_events.csv.

    CSV columns: event_type, ts_utc (ISO-8601 with offset). Returns [] if the
    file is missing so a misconfigured path degrades to "no events" rather than
    crashing the live runner build.
    """
    import csv
    import os

    if not os.path.exists(path):
        log.warning("news_straddle: events file %s not found; no events loaded", path)
        return []
    out: list[datetime] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("event_type") == event_type:
                out.append(datetime.fromisoformat(row["ts_utc"]))
    return sorted(out)


@dataclass(frozen=True)
class ResolvedStraddleSpec:
    event_type: str
    instrument: str
    offset_ticks: int
    tp_r: Decimal
    contracts: int
    suppress_base: bool


def resolve_straddle_specs(strategy_cfg, default_instrument: str) -> list[ResolvedStraddleSpec]:
    """Per-event specs when news_straddle_events is populated; otherwise a single
    spec built from the legacy single-event fields (back-compat — keeps the existing
    CPI/MNQ live config working when the list is empty)."""
    events = getattr(strategy_cfg, "news_straddle_events", None) or []
    if events:
        return [
            ResolvedStraddleSpec(
                event_type=e.event_type, instrument=e.instrument,
                offset_ticks=e.offset_ticks, tp_r=e.tp_r,
                contracts=e.contracts, suppress_base=e.suppress_base,
            )
            for e in events
        ]
    return [ResolvedStraddleSpec(
        event_type=strategy_cfg.news_straddle_event_type,
        instrument=default_instrument,
        offset_ticks=strategy_cfg.news_straddle_offset_ticks,
        tp_r=strategy_cfg.news_straddle_tp_r,
        contracts=strategy_cfg.news_straddle_contracts,
        suppress_base=strategy_cfg.cpi_base_suppress,
    )]


@dataclass
class NewsStraddleConfig:
    instrument: str
    event_times: list[datetime]               # release timestamps, UTC-aware
    offset_ticks: int = 60                     # stop-entry offset past the range
    tp_r: Decimal = Decimal("3.0")             # target = tp_r × R (R = offset)
    tick: Decimal = Decimal("0.25")            # price tick (NQ/MNQ = 0.25)
    range_minutes: int = 15                    # pre-release range window
    entry_window_minutes: int = 30             # window after release to fill
    max_hold_minutes: int = 180                # flatten after this (oracle cap)
    min_range_bars: int = 1                    # require this many pre-range bars


@dataclass
class _Event:
    ts: datetime
    status: str = _PENDING
    rhigh: Decimal | None = None
    rlow: Decimal | None = None
    nbars: int = 0
    side: str = ""
    fill_ts: datetime | None = None


class NewsStraddleDetector:
    """Streaming: feed closed bars, get at most one Signal per scheduled event."""

    def __init__(self, config: NewsStraddleConfig) -> None:
        self.config = config
        self.events: list[_Event] = [
            _Event(ts=t) for t in sorted(config.event_times)
        ]
        self._offset = Decimal(config.offset_ticks) * config.tick
        self.whipsaws = 0
        # exit_request: read+cleared by ExecutionEngine each bar (max-hold flatten).
        self.exit_request: str | None = None

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        for ev in self.events:
            if ev.status in (_FIRED, _WHIPSAW, _NOFILL, _SKIPPED):
                # Terminal — but a fired event may still need a max-hold flatten.
                if (ev.status == _FIRED and ev.fill_ts is not None
                        and bar.ts >= ev.fill_ts + timedelta(minutes=self.config.max_hold_minutes)):
                    ev.fill_ts = None  # fire the request once
                    self.exit_request = "news_max_hold"
                continue

            range_start = ev.ts - timedelta(minutes=self.config.range_minutes)
            window_end = ev.ts + timedelta(minutes=self.config.entry_window_minutes)

            # Pre-release range accumulation (bars strictly before the release).
            if range_start <= bar.ts < ev.ts:
                ev.rhigh = bar.high if ev.rhigh is None else max(ev.rhigh, bar.high)
                ev.rlow = bar.low if ev.rlow is None else min(ev.rlow, bar.low)
                ev.nbars += 1
                continue

            # Entry window. First bar at/after release arms (locks) the range.
            if ev.ts <= bar.ts <= window_end:
                if ev.status == _PENDING:
                    if ev.nbars < self.config.min_range_bars or ev.rhigh is None:
                        ev.status = _SKIPPED
                        log.info("news_straddle: %s skipped (%d pre-range bars < %d)",
                                 ev.ts, ev.nbars, self.config.min_range_bars)
                        continue
                    ev.status = _ARMED
                    log.info("news_straddle armed: %s range=[%s-%s] buy=%s sell=%s",
                             ev.ts, ev.rlow, ev.rhigh,
                             ev.rhigh + self._offset, ev.rlow - self._offset)
                sig = self._check_break(ev, bar)
                if sig is not None:
                    return sig
                continue

            # Past the entry window without a fill.
            if bar.ts > window_end and ev.status in (_ARMED, _PENDING):
                ev.status = _NOFILL
                log.info("news_straddle: %s nofill (entry window elapsed)", ev.ts)
        return None

    def _check_break(self, ev: _Event, bar: Bar) -> Optional[Signal]:
        buy = ev.rhigh + self._offset
        sell = ev.rlow - self._offset
        up = bar.high >= buy
        dn = bar.low <= sell
        if up and dn:
            ev.status = _WHIPSAW
            self.whipsaws += 1
            log.info("news_straddle whipsaw: %s both legs tagged in one bar", ev.ts)
            return None
        if up:
            return self._fire(ev, bar, "long", entry=buy, stop=ev.rhigh)
        if dn:
            return self._fire(ev, bar, "short", entry=sell, stop=ev.rlow)
        return None

    def _fire(self, ev: _Event, bar: Bar, side: str, entry: Decimal, stop: Decimal) -> Signal:
        ev.status = _FIRED
        ev.side = side
        ev.fill_ts = bar.ts
        r = abs(entry - stop)
        target = entry + r * self.config.tp_r if side == "long" else entry - r * self.config.tp_r
        log.info("news_straddle %s: %s entry=%s stop=%s target=%s (R=%s, %s)",
                 side, self.config.instrument, entry, stop, target, r, ev.ts)
        return Signal(
            instrument=self.config.instrument,
            side=side,
            entry=entry,
            stop=stop,
            target=target,
            created_at=bar.ts,
            killzone="NEWS",
            sweep_pattern="NEWS_STRADDLE",
            sweep_extreme=stop,
            fvg_low=None,
            fvg_high=None,
            rationale=(f"News straddle {ev.ts:%Y-%m-%d %H:%M}Z: {side} break of "
                       f"[{ev.rlow}-{ev.rhigh}] offset {self.config.offset_ticks}t, "
                       f"stop {stop} (R={r}), target {target}"),
            sweep_bar_range=ev.rhigh - ev.rlow,
        )

    def state(self) -> dict:
        """Live dashboard view (Rule 13). Never mutates."""
        return {
            "whipsaws": self.whipsaws,
            "events": [
                {
                    "ts": ev.ts.isoformat(),
                    "status": ev.status,
                    "range_high": str(ev.rhigh) if ev.rhigh is not None else None,
                    "range_low": str(ev.rlow) if ev.rlow is not None else None,
                    "side": ev.side or None,
                }
                for ev in self.events
            ],
        }


@dataclass
class NewsStraddleComposer:
    """Stop-fill hook. The straddle does not re-arm after a stop (one per event)."""

    detector: NewsStraddleDetector

    def on_stop_loss(self) -> None:
        return None


@dataclass
class NewsStraddleRunner:
    """Duck-type of the StrategyRunner surface ExecutionEngine touches."""

    instrument: str
    timeframe: str
    detector: NewsStraddleDetector
    strategy_cfg: StrategyParams
    vp: None = None
    signal_instrument: str = ""
    last_reject: None = field(default=None, init=False)
    composer: NewsStraddleComposer = field(default=None)
    grader: SetupGrader = field(default_factory=SetupGrader)
    exit_request: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.composer is None:
            self.composer = NewsStraddleComposer(detector=self.detector)

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        sig = self.detector.on_bar(bar)
        # Surface the detector's max-hold flatten request to the engine.
        if self.detector.exit_request is not None:
            self.exit_request = self.detector.exit_request
            self.detector.exit_request = None
        return sig


def build_news_straddle_schedulers(broker, specs, events_path: str, arm_lead_seconds: int):
    """One NewsStraddleScheduler per resolved spec — each single-instrument with its
    own offset/tp_r/size and only its own event_type's release times."""
    from app.broker.paper import TICK_SIZE
    from app.notifications.news_straddle_scheduler import NewsStraddleScheduler

    schedulers = []
    for s in specs:
        schedulers.append(NewsStraddleScheduler(
            broker,
            instrument=s.instrument,
            event_times=load_event_times(events_path, s.event_type),
            offset_ticks=s.offset_ticks,
            tp_r=s.tp_r,
            tick=TICK_SIZE.get(s.instrument, Decimal("0.25")),
            size=s.contracts,
            arm_lead_seconds=arm_lead_seconds,
        ))
    return schedulers
