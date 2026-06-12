# Combined iFVG+ORB Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run both engines on one instrument in one process (`engine="combined"`) and benchmark the combined monthly-Combine pass rate vs the solo engines (naive union: 9/17 test, 7/12 2024).

**Architecture:** `app/strategy/combined.py` provides `CombinedRunner`, a composite that feeds every bar to both sub-runners and returns at most one Signal per bar (primary=iFVG preferred on the rare same-bar collision). It delegates the engine-facing attributes (`instrument`, `timeframe`, `strategy_cfg`, `vp`, `composer`, `grader`, `last_reject`, `signal_instrument`) to the iFVG primary, so **no changes to ExecutionEngine, server.py, or main.py's runners dict** — the engine sees one runner. Reversal/pretrade semantics are unchanged by construction: signals are source-agnostic, so an ORB signal opposite an open iFVG position flattens-and-reverses exactly like today (documented as the chosen risk-interaction semantic). Each signal sizes at the same risk_per_trade_pct; DLL/DPL/MLL are naturally shared (one RiskState).

**Tech Stack:** Python 3 / pytest / existing stack.

---

## Decided semantics (surface in report)

1. **One net position per instrument, reversals allowed across engines** (engine as-is). No per-engine budgets, no concurrency.
2. **Same-bar collision → iFVG wins** (graded setup beats clock breakout), logged at INFO.
3. ORB sub-config comes from the same StrategyParams (`orb_*` fields); benchmark uses the validated winner via `--set orb_r_multiple=2.5` (anchor 09:30 / range 15 are already defaults).
4. TP1-coupling check (verified in code): grader swings only populate when `htf_target_enabled` — off in frontier — so ORB signals can't inherit iFVG structural TP1s in this benchmark.

### Task 1: CombinedRunner (TDD)

**Files:**
- Create: `app/strategy/combined.py`
- Create: `tests/test_combined_runner.py`

- [ ] **Step 1: Failing tests** — `tests/test_combined_runner.py`:

```python
"""CombinedRunner: both sub-runners see every bar; one signal out per bar;
iFVG primary wins same-bar collisions; engine-facing attrs delegate."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from app.broker.events import Bar
from app.strategy.combined import CombinedRunner


def _bar():
    return Bar(instrument="MNQ", timeframe="5min",
               ts=datetime(2026, 3, 4, 14, 30, tzinfo=timezone.utc),
               open=Decimal("1"), high=Decimal("2"), low=Decimal("1"),
               close=Decimal("2"), volume=1)


@dataclass
class _StubRunner:
    instrument: str = "MNQ"
    timeframe: str = "5min"
    strategy_cfg: object = None
    vp: object = None
    composer: object = None
    grader: object = None
    signal_instrument: str = ""
    last_reject: object = None
    queued: list = field(default_factory=list)
    bars_seen: int = 0

    def on_bar(self, bar):
        self.bars_seen += 1
        return self.queued.pop(0) if self.queued else None


class TestCombinedRunner:
    def test_both_subrunners_see_every_bar(self):
        a, b = _StubRunner(), _StubRunner()
        c = CombinedRunner(primary=a, secondary=b)
        for _ in range(3):
            c.on_bar(_bar())
        assert a.bars_seen == 3 and b.bars_seen == 3

    def test_secondary_signal_passes_through(self):
        a, b = _StubRunner(), _StubRunner(queued=["orb-sig"])
        assert CombinedRunner(primary=a, secondary=b).on_bar(_bar()) == "orb-sig"

    def test_primary_wins_collision(self):
        a, b = _StubRunner(queued=["ifvg-sig"]), _StubRunner(queued=["orb-sig"])
        assert CombinedRunner(primary=a, secondary=b).on_bar(_bar()) == "ifvg-sig"

    def test_delegates_engine_facing_attrs(self):
        a = _StubRunner(instrument="MNQ", last_reject="rej")
        c = CombinedRunner(primary=a, secondary=_StubRunner())
        assert c.instrument == "MNQ"
        assert c.last_reject == "rej"
        assert c.vp is a.vp and c.grader is a.grader and c.composer is a.composer
```

- [ ] **Step 2: Verify failure** — `pytest tests/test_combined_runner.py -q` → ModuleNotFoundError

- [ ] **Step 3: Implement `app/strategy/combined.py`:**

```python
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
    def grader(self):
        return self.primary.grader

    @property
    def signal_instrument(self):
        return self.primary.signal_instrument

    @property
    def last_reject(self):
        return self.primary.last_reject
```

- [ ] **Step 4: Verify pass** — `pytest tests/test_combined_runner.py -q`

- [ ] **Step 5: Commit** — `git add app/strategy/combined.py tests/test_combined_runner.py && git commit -m "feat: CombinedRunner - iFVG+ORB behind one runner interface"`

### Task 2: engine="combined" selection

**Files:**
- Modify: `app/backtest/runner.py` (`_build_runner` orb branch)
- Modify: `app/main.py` (`_build_runner` orb branch)
- Test: `tests/test_combined_runner.py`

- [ ] **Step 1: Failing test** — append:

```python
class TestEngineSelection:
    def test_build_runner_combined(self):
        from app.backtest.runner import BacktestConfig, _build_runner
        from app.bot_config import StrategyParams
        from app.execution.engine import StrategyRunner
        from app.strategy.orb import ORBRunner

        s = StrategyParams(engine="combined", orb_r_multiple=Decimal("2.5"))
        cfg = BacktestConfig(instrument="MNQ", bars=iter([]),
                             timeframe="5min", strategy_params=s)
        runner = _build_runner(cfg)
        assert isinstance(runner.primary, StrategyRunner)
        assert isinstance(runner.secondary, ORBRunner)
        assert runner.secondary.detector.config.r_multiple == Decimal("2.5")
        assert runner.instrument == "MNQ"
```

- [ ] **Step 2: Implement** — in `app/backtest/runner.py` `_build_runner`, extend the engine branch (before the orb check):

```python
        if s.engine == "combined":
            from app.strategy.combined import CombinedRunner
            primary = _build_runner(dataclasses.replace(
                cfg, strategy_params=s.model_copy(update={"engine": "ifvg"})))
            secondary = _build_runner(dataclasses.replace(
                cfg, strategy_params=s.model_copy(update={"engine": "orb"})))
            return CombinedRunner(primary=primary, secondary=secondary)
```

Same shape in `app/main.py` `_build_runner` (recursing with `s.model_copy(update={"engine": ...})` and the function's own args).

- [ ] **Step 3: Verify** — `pytest tests/test_combined_runner.py tests/test_orb.py tests/test_backtest_runner.py -q`

Note: `run_backtest` reads `runner.grader` for the HTF feed → delegates to the iFVG grader (correct). `engine.htf_bias` setup reads cfg only (unchanged).

- [ ] **Step 4: Smoke** — one month: `python scripts/run_monthly_combine.py --bars bars/bars_MNQ_train_2024.csv --instrument MNQ --timeframe 5min --risk-pct 1.25 --set engine=combined --set orb_r_multiple=2.5 --window 12` → expect trades > either solo engine's window-12 run; no exceptions.

- [ ] **Step 5: Commit**

### Task 3: Combined benchmark runs

- [ ] Run both periods:

```
... --set engine=combined --set orb_r_multiple=2.5 --save-id comb_ifvg_orb_test --save-label "Combined iFVG+ORB r2.5 (test 17mo)"
... --set engine=combined --set orb_r_multiple=2.5 --save-id comb_ifvg_orb_2024 --save-label "Combined iFVG+ORB r2.5 (2024)"
```

- [ ] Score: vs iFVG control (6/17, 4/12), vs ORB solo (4/17, 4/12), vs naive union (9/17, 7/12). Per-month: did the union months survive sharing one account (DLL/DPL/soft-buffer interactions)? Any new MLL fails?

- [ ] Report `trade_analysis/2026-06-11_combined_engine.md` + memory update + commit.
