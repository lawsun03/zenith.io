# ORB Second Engine — Solo Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the ORB (Opening Range Breakout) detector as a standalone second engine and benchmark it SOLO through the monthly-Combine harness, scoring month-complementarity against the iFVG engine's drought months.

**Architecture:** `app/strategy/orb.py` provides `ORBDetector` (clock-driven, closed-bar-confirmed breakouts of the opening range) and `ORBRunner`, a duck-type of the `StrategyRunner` interface the engine actually touches (`instrument`, `timeframe`, `strategy_cfg`, `vp=None`, `on_bar`, `last_reject`, `composer.on_stop_loss`, `grader._htf_swing_*`, `signal_instrument`). `StrategyParams.engine = "ifvg"|"orb"` selects the runner in both `_build_runner` sites; the monthly harness needs zero changes (`--set engine=orb --set orb_r_multiple=2.0 ...`).

**Tech Stack:** Python 3 / pytest / existing backtest stack. No engine/PaperBroker changes.

---

## Fixed context (verified)

- Design + evaluation path agreed 2026-06-11 (memory `project_orb_second_engine.md`). Lawrence chose to sweep BOTH anchors (9:30 ET cash open and 8:30 ET data open).
- Bars: 1min CSVs aggregated to 5min by `load_bars_csv` (bar.ts = bucket start). 15min OR = 3 bars, 30min OR = 6 bars.
- Closed-bar confirmation only (forming-bar lesson). Entry-cutoff (14:30 CT) and flatten (15:05 CT) are enforced by the engine in all backtests — the detector doesn't reimplement them.
- Engine TP1 lookup reads `runner.grader._htf_swing_highs/_lows`: a fresh `SetupGrader` has empty lists → `tp1_price=None` → falls back to `partial_profit_r`. So ORB runs get standard 1.5R partials (harness default) — fine for the benchmark.
- The grader is NOT in the ORB signal path (no grading; `setup_grade=None` is handled everywhere: backtest `on_signal` guards `g is not None`).
- `Signal` requires `killzone`, `sweep_pattern`, `sweep_extreme`, `sweep_bar_range`; ORB fills them with `"ORB"`, `"ORB"`, the broken range edge, and the OR height.
- Train/test discipline: full 16-config sweep on **2024**; only the top ~3 validate on the 2025-26 test 17mo. Complementarity is judged on the test period vs `ab_control_test` per-month results (iFVG droughts: 2025-01/03/04/05/06/10/11, 2026-01/03/04 — months with 3–15 trades and no pass).

## Success criteria (from memory + ablation campaign)

- Solo pass rate is secondary; **KEY METRIC: does ORB profit/pass in iFVG drought months?**
- Also record: trades/mo (should be ~20 by construction), PF, maxDD, MLL fails, per-side PF (harness reports all of this since the ablation campaign).
- If complementary → next phase (not this plan): two-runner combined process (needs the `engine.runners` dict-keying change).

---

### Task 1: ORB detector + runner (TDD)

**Files:**
- Create: `app/strategy/orb.py`
- Create: `tests/test_orb.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orb.py`:

```python
"""ORB detector tests — opening range, closed-bar breakout confirmation,
one-trade-per-day, daily reset, both anchors."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.sim.events import Bar
from app.strategy.orb import ORBConfig, ORBDetector

ET = ZoneInfo("America/New_York")


def bar(h, m, o, hi, lo, c, day=4):
    return Bar(instrument="MNQ", timeframe="5min",
               ts=datetime(2026, 3, day, h, m, tzinfo=ET),
               open=Decimal(o), high=Decimal(hi), low=Decimal(lo),
               close=Decimal(c), volume=100)


def _cfg(**kw):
    return ORBConfig(instrument="MNQ", **kw)


def feed_range(det, day=4):
    """Three 5min bars from 9:30 ET → OR = 21000–21020."""
    assert det.on_bar(bar(9, 30, "21005", "21015", "21000", "21010", day)) is None
    assert det.on_bar(bar(9, 35, "21010", "21020", "21005", "21018", day)) is None
    assert det.on_bar(bar(9, 40, "21018", "21019", "21008", "21012", day)) is None


class TestORB:
    def test_long_breakout_on_close_beyond_range(self):
        det = ORBDetector(_cfg(range_minutes=15, r_multiple=Decimal("2.0")))
        feed_range(det)
        # wick above OR high but close inside -> no signal (close-confirmed)
        assert det.on_bar(bar(9, 45, "21012", "21025", "21010", "21015")) is None
        sig = det.on_bar(bar(9, 50, "21015", "21030", "21014", "21028"))
        assert sig is not None
        assert sig.side == "long"
        assert sig.entry == Decimal("21028")          # breakout bar close
        assert sig.stop == Decimal("21000")           # opposite OR edge
        # target = entry + 2R, R = 28
        assert sig.target == Decimal("21084")
        assert sig.killzone == "ORB"

    def test_short_breakout(self):
        det = ORBDetector(_cfg())
        feed_range(det)
        sig = det.on_bar(bar(9, 45, "21010", "21012", "20985", "20990"))
        assert sig is not None
        assert sig.side == "short"
        assert sig.stop == Decimal("21020")

    def test_one_trade_per_day_and_daily_reset(self):
        det = ORBDetector(_cfg())
        feed_range(det, day=4)
        assert det.on_bar(bar(9, 45, "21015", "21030", "21014", "21028", 4)) is not None
        # second breakout same day suppressed
        assert det.on_bar(bar(10, 0, "21028", "21040", "21025", "21039", 4)) is None
        # next day: fresh range, fires again
        feed_range(det, day=5)
        assert det.on_bar(bar(9, 45, "21015", "21030", "21014", "21028", 5)) is not None

    def test_no_signal_inside_range_or_before_open(self):
        det = ORBDetector(_cfg())
        assert det.on_bar(bar(9, 25, "21000", "21100", "20900", "21050")) is None
        feed_range(det)
        assert det.on_bar(bar(9, 45, "21012", "21019", "21001", "21010")) is None

    def test_830_anchor(self):
        det = ORBDetector(_cfg(open_et="08:30", range_minutes=15))
        assert det.on_bar(bar(8, 30, "21005", "21015", "21000", "21010")) is None
        assert det.on_bar(bar(8, 35, "21010", "21020", "21005", "21018")) is None
        assert det.on_bar(bar(8, 40, "21018", "21019", "21008", "21012")) is None
        sig = det.on_bar(bar(8, 45, "21015", "21030", "21014", "21028"))
        assert sig is not None and sig.side == "long"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_orb.py -q` — Expected: FAIL, `ModuleNotFoundError: app.strategy.orb`

- [ ] **Step 3: Implement `app/strategy/orb.py`**

```python
"""
ORB — Opening Range Breakout detector (second signal engine).

Clock-driven: the opening range is the high/low of the first
`range_minutes` after `open_et`; a later bar CLOSING beyond an edge
fires a Signal (closed-bar confirmation only — the forming-bar lesson).
Stop = opposite range edge; target = fixed R multiple. One signal per
trading day by default. No FVGs, no sweeps, no grader.

ORBRunner duck-types the slice of StrategyRunner the engine touches, so
it plugs into ExecutionEngine/run_backtest unchanged (solo benchmark).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from app.bot_config import StrategyParams
from app.sim.events import Bar
from app.strategy.composer import Signal
from app.strategy.grader import SetupGrader

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


@dataclass
class ORBConfig:
    instrument: str
    open_et: str = "09:30"          # "HH:MM" ET; 09:30 = cash open, 08:30 = data open
    range_minutes: int = 15
    r_multiple: Decimal = Decimal("2.0")
    max_trades_per_day: int = 1


class ORBDetector:
    """Streaming: feed closed bars, get at most one Signal per day."""

    def __init__(self, config: ORBConfig) -> None:
        self.config = config
        hh, mm = config.open_et.split(":")
        self._open_t = time(int(hh), int(mm))
        self._day: date | None = None
        self._or_high: Decimal | None = None
        self._or_low: Decimal | None = None
        self._fired = 0

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        et = bar.ts.astimezone(ET)
        if et.date() != self._day:
            self._day = et.date()
            self._or_high = self._or_low = None
            self._fired = 0

        start = datetime.combine(et.date(), self._open_t, tzinfo=ET)
        end = start + timedelta(minutes=self.config.range_minutes)

        if et < start:
            return None
        if et < end:  # building the opening range
            self._or_high = bar.high if self._or_high is None else max(self._or_high, bar.high)
            self._or_low = bar.low if self._or_low is None else min(self._or_low, bar.low)
            return None
        if self._or_high is None or self._or_low is None:
            return None  # no bars landed in the range window (holiday/gap)
        if self._fired >= self.config.max_trades_per_day:
            return None

        if bar.close > self._or_high:
            side, stop, broken = "long", self._or_low, self._or_high
        elif bar.close < self._or_low:
            side, stop, broken = "short", self._or_high, self._or_low
        else:
            return None

        entry = bar.close
        r = abs(entry - stop)
        if r == 0:
            return None
        target = entry + r * self.config.r_multiple if side == "long" \
            else entry - r * self.config.r_multiple
        self._fired += 1
        log.info("ORB breakout: %s %s close=%s OR=[%s-%s] stop=%s target=%s",
                 self.config.instrument, side, entry,
                 self._or_low, self._or_high, stop, target)
        return Signal(
            instrument=self.config.instrument,
            side=side,
            entry=entry,
            stop=stop,
            target=target,
            created_at=bar.ts,
            killzone="ORB",
            sweep_pattern="ORB",
            sweep_extreme=broken,
            fvg_low=None,
            fvg_high=None,
            rationale=(f"ORB {self.config.open_et}+{self.config.range_minutes}min: "
                       f"{side} breakout close {entry} of OR "
                       f"{self._or_low}-{self._or_high}"),
            sweep_bar_range=self._or_high - self._or_low,
        )


class _NoopComposer:
    """Stop-fill hook the engine calls on every runner; ORB has no cooldown."""

    def on_stop_loss(self) -> None:
        pass


@dataclass
class ORBRunner:
    """Duck-type of the StrategyRunner surface ExecutionEngine touches."""

    instrument: str
    timeframe: str
    detector: ORBDetector
    strategy_cfg: StrategyParams
    vp: None = None                       # engine skips VP when None
    signal_instrument: str = ""
    last_reject: None = field(default=None, init=False)
    composer: _NoopComposer = field(default_factory=_NoopComposer)
    grader: SetupGrader = field(default_factory=SetupGrader)  # empty swings → no TP1

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        return self.detector.on_bar(bar)
```

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_orb.py -q` — Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/strategy/orb.py tests/test_orb.py
git commit -m "feat: ORB detector + runner (second engine, solo benchmark)"
```

---

### Task 2: Engine selection via StrategyParams

**Files:**
- Modify: `app/bot_config.py` (`StrategyParams`, after `confirmation`)
- Modify: `app/backtest/runner.py` (`_build_runner`, top of faithful path)
- Modify: `app/main.py` (`_build_runner`, same branch)
- Test: `tests/test_orb.py`

- [ ] **Step 1: Failing test** — append to `tests/test_orb.py`:

```python
class TestEngineSelection:
    def test_build_runner_returns_orb_runner(self):
        from app.backtest.runner import BacktestConfig, _build_runner
        from app.bot_config import StrategyParams
        from app.strategy.orb import ORBRunner

        s = StrategyParams(engine="orb", orb_range_minutes=30,
                           orb_r_multiple=Decimal("2.5"), orb_open_et="08:30")
        cfg = BacktestConfig(instrument="MNQ", bars=iter([]),
                             timeframe="5min", strategy_params=s)
        runner = _build_runner(cfg)
        assert isinstance(runner, ORBRunner)
        assert runner.detector.config.range_minutes == 30
        assert runner.detector.config.open_et == "08:30"
        assert runner.detector.config.r_multiple == Decimal("2.5")
```

Run: `python -m pytest tests/test_orb.py::TestEngineSelection -q` — Expected: FAIL (`engine` unknown field)

- [ ] **Step 2: Implement**

`app/bot_config.py`, `StrategyParams` after `confirmation`:

```python
    # Second engine selection: "ifvg" (default) | "orb". ORB = opening range
    # breakout — clock-driven, fires ~daily; attacks iFVG drought months.
    engine: str = "ifvg"
    orb_open_et: str = "09:30"            # "09:30" cash open | "08:30" data open
    orb_range_minutes: int = 15
    orb_r_multiple: Decimal = Decimal("2.0")
    orb_max_trades_per_day: int = 1
```

`app/backtest/runner.py` `_build_runner`, at the top of the faithful path (right after `s = cfg.strategy_params`):

```python
    if cfg.strategy_params is not None:
        s = cfg.strategy_params
        if s.engine == "orb":
            from app.strategy.orb import ORBConfig, ORBDetector, ORBRunner
            return ORBRunner(
                instrument=cfg.instrument,
                timeframe=cfg.timeframe,
                detector=ORBDetector(ORBConfig(
                    instrument=cfg.instrument,
                    open_et=s.orb_open_et,
                    range_minutes=s.orb_range_minutes,
                    r_multiple=s.orb_r_multiple,
                    max_trades_per_day=s.orb_max_trades_per_day,
                )),
                strategy_cfg=s,
            )
```

`app/main.py` `_build_runner`: same branch at the top of the function body (after `zones`/`s` are derived), identical code but `instrument=instrument` and add `signal_instrument=signal_instrument or ""`.

Note: `run_backtest`'s HTF refresh calls `r.grader for r in [runner]` and `_refresh_backtest_htf` feeds `g.update_delivery_fvgs` — ORBRunner.grader is a real SetupGrader, so this is harmless.

- [ ] **Step 3: Run tests** — `python -m pytest tests/test_orb.py tests/test_backtest_runner.py -q` — Expected: PASS

- [ ] **Step 4: Smoke run one month through the harness**

```
python scripts/run_monthly_combine.py --bars bars/bars_MNQ_train_2024.csv --instrument MNQ --timeframe 5min --risk-pct 1.25 --set engine=orb --window 12
```

Expected: runs clean, ~150–250 trades over the year (≈1/day minus no-breakout days), killzone tag "ORB" (visible in saved by_killzone if saved). If 0 trades → debug detector wiring before the sweep.

- [ ] **Step 5: Commit**

```bash
git add app/bot_config.py app/backtest/runner.py app/main.py tests/test_orb.py
git commit -m "feat: engine=orb selection in StrategyParams + both _build_runner sites"
```

---

### Task 3: 2024 sweep (16 configs)

- [ ] **Step 1: Run the grid on 2024 (train)** — anchors {09:30, 08:30} × range {15, 30} × r {1.5, 2.0, 2.5, 3.0}:

```
python scripts/run_monthly_combine.py --bars bars/bars_MNQ_train_2024.csv --instrument MNQ --timeframe 5min --risk-pct 1.25 --set engine=orb --set orb_open_et=<A> --set orb_range_minutes=<M> --set orb_r_multiple=<R> --save-id orb_<a><m>_r<r>_2024 --save-label "ORB <A>+<M>min r<R> (2024)"
```

save-id scheme: `orb_930x15_r20_2024`, `orb_830x30_r25_2024`, etc. Run 4–6 in parallel.

- [ ] **Step 2: Tabulate** — pass rate, trades/mo, run PF, worst maxDD, MLL fails, side split per config. Pick top ~3 by (PF, pass rate) with sane maxDD.

---

### Task 4: Test-period validation + complementarity + report

- [ ] **Step 1: Run top ~3 configs on `bars/bars_MNQ_test_2025_2026.csv`** (`--save-id orb_<cfg>_test`).

- [ ] **Step 2: Complementarity table** — per-month ORB net/pass vs `ab_control_test` months. Drought months to check: 2025-01, 2025-03, 2025-04, 2025-05, 2025-06, 2025-10, 2025-11, 2026-01, 2026-03, 2026-04.

- [ ] **Step 3: Report + memory + commit** — `trade_analysis/<date>_orb_benchmark.md` (sweep table, complementarity verdict, go/no-go for the two-runner build); update `project_orb_second_engine.md` memory; commit docs + code.

---

## Self-review notes
- Spec coverage: detector+tests (memory step 1) → Task 1; harness path (step 2) → Task 2–3; complementarity (step 3) → Task 4; steps 4–5 (combined process, funded_sim) are explicitly out of scope for this plan.
- Types consistent: `engine`, `orb_open_et`, `orb_range_minutes`, `orb_r_multiple`, `orb_max_trades_per_day` named identically in StrategyParams, ORBConfig wiring, tests, and CLI.
- No placeholders; all code complete.
