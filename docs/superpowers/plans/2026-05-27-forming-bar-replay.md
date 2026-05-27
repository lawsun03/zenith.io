# Forming-Bar-Aware Backtest Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replay 5s intrabar data through the real strategy + engine so the backtest reproduces live's forming-bar entries, with bracket fills resolved at 5s resolution.

**Architecture:** A deterministic, sample-driven driver (`app/backtest/intrabar_replay.py`) feeds the existing `ExecutionEngine` (reused for the gate + risk-sizing + VP) and a `PaperBroker` extended to resolve fills against individual 5s samples. The engine's forming-poll body is extracted into a callable (`evaluate_forming_bar`) used by both the live poll and the driver, so there is no duplicated detection logic. The live path and the existing completed-bar backtest are behavior-preserved.

**Tech Stack:** Python 3.12, asyncio, Decimal, pytest. Reuses `BacktestConfig`, `_build_runner`, `_compute_stats`, `_reconstruct_trades` from `app/backtest/runner.py`.

**Spec:** `docs/superpowers/specs/2026-05-27-forming-bar-replay-design.md`

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `app/execution/engine.py` | Extract `evaluate_forming_bar(instrument, runner, forming_bar)` from `_poll_forming_bars` (behavior-preserving); poll calls it |
| Modify | `app/broker/paper.py` | Extract `_resolve_bracket(bracket, high, low, ts)`; `inject_bar` uses it; add `resolve_sample(sample)` |
| Create | `app/backtest/intrabar_replay.py` | `IntrabarData`, `load_intrabar_csv`, `run_intrabar_backtest` |
| Create | `scripts/validate_intrabar_replay.py` | Acceptance: run on `intrabar_MGC.csv`, print signals for count+character check |
| Create | `tests/test_intrabar_replay.py` | Loader, `resolve_sample`, partials-across-samples, same-minute entry+exit, dedup, driver smoke + determinism |
| Modify | `tests/test_engine.py` | Direct test that `evaluate_forming_bar` places an order |

---

## Task 1: Extract `evaluate_forming_bar` in the engine (behavior-preserving)

**Files:**
- Modify: `app/execution/engine.py`
- Modify: `tests/test_engine.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_engine.py`, add at the end:

```python
def test_evaluate_forming_bar_places_order():
    """evaluate_forming_bar runs the gate + places a bracket when a runner
    yields a forming signal. This is the method the replay driver reuses so
    backtest and live share one forming-entry path."""
    import asyncio
    from types import SimpleNamespace
    from app.broker.paper import PaperBroker
    from app.broker.events import Bar
    from app.execution.engine import ExecutionEngine
    from app.risk.config import fifty_k_combine
    from app.risk.state import RiskState
    from app.strategy.composer import Signal

    sig = Signal(
        instrument="MGC", side="long",
        entry=Decimal("100"), stop=Decimal("99"), target=Decimal("102"),
        created_at=in_ny_am(0), killzone="NY AM", sweep_pattern="B_one_bar",
        sweep_extreme=Decimal("99"), fvg_low=None, fvg_high=None, rationale="forming test",
    )
    b2 = Bar(instrument="MGC", timeframe="1min", ts=in_ny_am(0),
             open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
             close=Decimal("100"), volume=10)
    fake_runner = SimpleNamespace(
        instrument="MGC", timeframe="1min", vp=None,
        displacement=SimpleNamespace(peek_displacement=lambda: ("bullish", b2, b2)),
        composer=SimpleNamespace(awaiting=[]),
        try_signal_from_forming=lambda fb: sig,
    )
    broker = PaperBroker(slippage_ticks_market=0, commission_per_side=Decimal("0"))
    rs = RiskState(config=fifty_k_combine())
    eng = ExecutionEngine(broker=broker, risk_state=rs, runners=[fake_runner],
                          replay_mode=True, contracts=1)

    async def go():
        await broker.connect()
        await eng.evaluate_forming_bar("MGC", fake_runner, b2)
        return await broker.get_positions()
    positions = asyncio.run(go())
    assert len(positions) == 1 and positions[0].side == "long"
```

(`in_ny_am` is an existing helper in `tests/test_engine.py`; reuse it. If a runner registered via `runners=[...]` must hash, `SimpleNamespace` works because `ExecutionEngine` keys `self.runners` by `r.instrument`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_engine.py::test_evaluate_forming_bar_places_order -v`
Expected: FAIL — `AttributeError: 'ExecutionEngine' object has no attribute 'evaluate_forming_bar'`.

- [ ] **Step 3: Add the method and call it from the poll**

In `app/execution/engine.py`, add this method immediately before `_poll_forming_bars`:

```python
    async def evaluate_forming_bar(self, instrument: str, runner: "StrategyRunner", forming_bar: Bar) -> None:
        """Evaluate one forming bar for one instrument: dedup per displacement
        bar, try a mid-bar signal, apply the VP gate, then act (gate + size +
        place). Shared by the live poll (_poll_forming_bars) and the backtest
        intrabar replay so both use one forming-entry path."""
        peek = runner.displacement.peek_displacement()
        if peek is None:
            return
        _side, _b1, b2 = peek
        if self._forming_signal_fired.get(instrument) == b2.ts:
            return

        signal = runner.try_signal_from_forming(forming_bar)
        if signal is None:
            return
        self._forming_signal_fired[instrument] = b2.ts

        if (
            runner.vp is not None
            and self.strategy_cfg is not None
            and self.strategy_cfg.vp_enabled
            and runner.vp.has_prior_profile()
        ):
            signal = runner.vp.apply(signal, self.strategy_cfg)
            if signal is None:
                return

        log.info("Forming bar signal: %s", signal.rationale)
        outcome = await self._act_on_signal(signal)
        if self.on_signal is not None:
            try:
                await self.on_signal(signal, outcome)
            except Exception:
                log.exception("on_signal callback raised (forming bar)")
```

Then replace the per-instrument body of `_poll_forming_bars` (the code inside `for instrument, runner in self.runners.items():`, currently lines ~515-563) with this:

```python
            for instrument, runner in self.runners.items():
                try:
                    get_fb = getattr(self.broker, "get_forming_bar", None)
                    if get_fb is None:
                        continue
                    forming_bar = await get_fb(runner.timeframe)
                    if forming_bar is None:
                        continue
                    await self.evaluate_forming_bar(instrument, runner, forming_bar)
                except Exception:
                    log.exception("_poll_forming_bars failed for %s", instrument)
```

- [ ] **Step 4: Run the new test + the full engine suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_engine.py -v`
Expected: the new test PASSES; the pre-existing `test_signal_denied_when_already_at_max_contracts` may still fail (known baseline) — NO other new failures (behavior-preserving extraction).

- [ ] **Step 5: Commit**

```bash
git add app/execution/engine.py tests/test_engine.py
git commit -m "refactor: extract evaluate_forming_bar for live+backtest reuse"
```

---

## Task 2: Extract `_resolve_bracket` in PaperBroker (behavior-preserving)

**Files:**
- Modify: `app/broker/paper.py`

- [ ] **Step 1: Add `_resolve_bracket` and route `inject_bar` through it**

In `app/broker/paper.py`, add this method directly above `inject_bar`:

```python
    async def _resolve_bracket(self, bracket: "_OpenBracket", high: Decimal, low: Decimal, ts: datetime) -> None:
        """Resolve one open bracket against a (high, low) range at time ts.
        Shared by inject_bar (completed-bar range) and resolve_sample (5s range)
        so fill semantics — partial, then pessimistic stop-first — are identical."""
        # Partial profit: if partial_target touched and not yet filled,
        # close partial_size contracts and move the stop to break-even.
        if (
            not bracket.partial_filled
            and bracket.partial_target is not None
            and bracket.partial_size > 0
        ):
            partial_hit = (
                (bracket.side == "long" and high >= bracket.partial_target)
                or (bracket.side == "short" and low <= bracket.partial_target)
            )
            if partial_hit:
                await self._close_partial(bracket, bracket.partial_target, ts)
                bracket.size -= bracket.partial_size
                bracket.stop = bracket.entry  # move stop to break-even
                bracket.partial_filled = True

        stop_hit = (
            (bracket.side == "long" and low <= bracket.stop)
            or (bracket.side == "short" and high >= bracket.stop)
        )
        target_hit = (
            (bracket.side == "long" and high >= bracket.target)
            or (bracket.side == "short" and low <= bracket.target)
        )
        if stop_hit and target_hit:
            exit_price = bracket.stop if self._pessimistic else bracket.target
            reason = "stop (whipsaw)" if self._pessimistic else "target (whipsaw)"
        elif stop_hit:
            exit_price = bracket.stop
            reason = "stop"
        elif target_hit:
            exit_price = bracket.target
            reason = "target"
        else:
            return
        is_stop_exit = "stop" in reason
        await self._close_bracket(bracket, exit_price, reason=reason, ts=ts, is_stop=is_stop_exit)
```

Then in `inject_bar`, replace the per-bracket resolution block (the partial-profit `if` through the `_close_bracket` call, currently lines ~310-351) with a single call. The loop body becomes:

```python
        for oid in list(self._open.keys()):
            bracket = self._open.get(oid)
            if bracket is None or bracket.instrument != bar.instrument:
                continue
            await self._resolve_bracket(bracket, bar.high, bar.low, bar.ts)
```

(Leave the `self._current_bar_ts`, `self._last_bar_close`, `_fanout`, and `_emit_equity_snapshot` lines in `inject_bar` unchanged.)

- [ ] **Step 2: Run the broker + partials + backtest suites**

Run: `.venv\Scripts\python.exe -m pytest tests/test_paper_broker_slippage.py tests/test_partial_profit.py tests/test_backtest_runner.py -q`
Expected: all pass (behavior-preserving — same fill logic, now factored).

- [ ] **Step 3: Commit**

```bash
git add app/broker/paper.py
git commit -m "refactor: extract PaperBroker._resolve_bracket from inject_bar"
```

---

## Task 3: Add `resolve_sample` to PaperBroker

**Files:**
- Modify: `app/broker/paper.py`
- Create: `tests/test_intrabar_replay.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_intrabar_replay.py`:

```python
"""Tests for 5s intrabar replay (loader, sample fills, driver)."""
import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from app.broker.paper import PaperBroker
from app.broker.events import Bar, Fill


def _bar(ts, o, h, l, c, instrument="MGC"):
    return Bar(instrument=instrument, timeframe="1min", ts=ts,
               open=Decimal(str(o)), high=Decimal(str(h)),
               low=Decimal(str(l)), close=Decimal(str(c)), volume=10)


def _ts(sec):
    return datetime(2026, 1, 2, 10, 0, sec, tzinfo=timezone.utc)


async def _open_long(broker, entry=100, stop=99, target=102, size=1):
    fills = []
    async def cap(f): fills.append(f)
    broker.on_fill(cap)
    await broker.connect()
    await broker.place_bracket("MGC", "long", size, Decimal(str(entry)),
                               Decimal(str(stop)), Decimal(str(target)))
    return fills


def test_resolve_sample_hits_target():
    broker = PaperBroker(slippage_ticks_market=0, commission_per_side=Decimal("0"))
    fills = asyncio.run(_run_resolve(broker, sample=_bar(_ts(5), 100, 102.5, 100, 101)))
    exits = [f for f in fills if not f.is_entry]
    assert len(exits) == 1 and exits[0].fill_price == Decimal("102")


def test_resolve_sample_hits_stop():
    broker = PaperBroker(slippage_ticks_market=0, commission_per_side=Decimal("0"))
    fills = asyncio.run(_run_resolve(broker, sample=_bar(_ts(5), 100, 100.5, 98.5, 99)))
    exits = [f for f in fills if not f.is_entry]
    assert len(exits) == 1 and exits[0].fill_price == Decimal("99")


def test_resolve_sample_both_hit_is_pessimistic_stop():
    broker = PaperBroker(slippage_ticks_market=0, commission_per_side=Decimal("0"))
    fills = asyncio.run(_run_resolve(broker, sample=_bar(_ts(5), 100, 102.5, 98.5, 100)))
    exits = [f for f in fills if not f.is_entry]
    assert len(exits) == 1 and exits[0].fill_price == Decimal("99")  # stop first


async def _run_resolve(broker, sample):
    fills = await _open_long(broker)
    await broker.resolve_sample(sample)
    return fills


def test_resolve_sample_no_touch_keeps_open():
    broker = PaperBroker(slippage_ticks_market=0, commission_per_side=Decimal("0"))
    fills = asyncio.run(_run_resolve(broker, sample=_bar(_ts(5), 100, 100.5, 99.5, 100)))
    exits = [f for f in fills if not f.is_entry]
    assert len(exits) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_intrabar_replay.py -k resolve_sample -v`
Expected: FAIL — `AttributeError: 'PaperBroker' object has no attribute 'resolve_sample'`.

- [ ] **Step 3: Implement `resolve_sample`**

In `app/broker/paper.py`, add directly after `inject_bar`:

```python
    async def resolve_sample(self, sample: Bar) -> None:
        """Resolve open brackets against a single sub-minute (5s) sample's
        high/low. Used by the intrabar replay driver. Does NOT fan the sample
        to bar handlers or emit equity — the driver owns strategy evaluation.
        Sets _current_bar_ts/_last_bar_close so a subsequent place_bracket
        stamps the sample's timestamp."""
        self._current_bar_ts = sample.ts
        self._last_bar_close[sample.instrument] = sample.close
        for oid in list(self._open.keys()):
            bracket = self._open.get(oid)
            if bracket is None or bracket.instrument != sample.instrument:
                continue
            await self._resolve_bracket(bracket, sample.high, sample.low, sample.ts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_intrabar_replay.py -k resolve_sample -v`
Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/broker/paper.py tests/test_intrabar_replay.py
git commit -m "feat: PaperBroker.resolve_sample for 5s intrabar fills"
```

---

## Task 4: Intrabar loader

**Files:**
- Create: `app/backtest/intrabar_replay.py`
- Modify: `tests/test_intrabar_replay.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_intrabar_replay.py`:

```python
from pathlib import Path
from app.backtest.intrabar_replay import load_intrabar_csv


def test_loader_groups_minutes_and_derives_completed(tmp_path: Path):
    p = tmp_path / "intrabar.csv"
    p.write_text(
        "sample_ts,instrument,bar_minute,open,high,low,close,volume\n"
        "2026-05-27T10:00:05+00:00,MGC,2026-05-27T10:00:00+00:00,100,100.5,99.8,100.2,5\n"
        "2026-05-27T10:00:10+00:00,MGC,2026-05-27T10:00:00+00:00,100,101.0,99.5,100.8,12\n"
        "2026-05-27T10:01:05+00:00,MGC,2026-05-27T10:01:00+00:00,100.8,101.2,100.6,101.0,7\n",
        encoding="utf-8",
    )
    data = load_intrabar_csv(p)
    assert data.minutes == ["2026-05-27T10:00:00+00:00", "2026-05-27T10:01:00+00:00"]
    # Two forming samples in the first minute, one in the second.
    assert len(data.forming[data.minutes[0]]) == 2
    # Completed bar for minute 0 = that minute's LAST sample's OHLC.
    c0 = data.completed[data.minutes[0]]
    assert c0.high == Decimal("101.0") and c0.low == Decimal("99.5") and c0.close == Decimal("100.8")
    assert c0.volume == 12
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_intrabar_replay.py -k loader -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.backtest.intrabar_replay'`.

- [ ] **Step 3: Create the module with the loader**

Create `app/backtest/intrabar_replay.py`:

```python
"""
Intrabar (5s) replay for the backtest.

Reproduces live's forming-bar entries by replaying sub-minute samples through
the real ExecutionEngine, with bracket fills resolved at 5s resolution via
PaperBroker.resolve_sample. See
docs/superpowers/specs/2026-05-27-forming-bar-replay-design.md.
"""
from __future__ import annotations

import csv
import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.broker.events import Bar, Fill
from app.broker.paper import PaperBroker
from app.execution.engine import ExecutionEngine, OrderOutcome
from app.risk.config import fifty_k_combine
from app.risk.state import RiskState
from app.strategy.composer import Signal
from app.backtest.runner import (
    BacktestConfig,
    BacktestResult,
    _build_runner,
    _compute_stats,
    _reconstruct_trades,
)

log = logging.getLogger(__name__)


@dataclass
class IntrabarData:
    minutes: list[str]                      # bar_minute keys, chronological
    forming: dict[str, list[Bar]]           # minute -> 5s forming Bars (in order)
    completed: dict[str, Bar]               # minute -> derived completed 1-min Bar


def load_intrabar_csv(path: Path, instrument: str = "MGC", timeframe: str = "1min") -> IntrabarData:
    """Parse a 5s intrabar snapshot CSV into per-minute forming bars + derived
    completed bars (each minute's last sample carries the full-minute OHLC)."""
    forming: "OrderedDict[str, list[Bar]]" = OrderedDict()
    last_row: "OrderedDict[str, dict]" = OrderedDict()
    with Path(path).open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            bm = r["bar_minute"]
            fb = Bar(
                instrument=instrument, timeframe=timeframe,
                ts=datetime.fromisoformat(bm),
                open=Decimal(r["open"]), high=Decimal(r["high"]),
                low=Decimal(r["low"]), close=Decimal(r["close"]),
                volume=int(r["volume"]),
            )
            forming.setdefault(bm, []).append(fb)
            last_row[bm] = r
    completed: dict[str, Bar] = {}
    for bm, r in last_row.items():
        completed[bm] = Bar(
            instrument=instrument, timeframe=timeframe,
            ts=datetime.fromisoformat(bm),
            open=Decimal(r["open"]), high=Decimal(r["high"]),
            low=Decimal(r["low"]), close=Decimal(r["close"]),
            volume=int(r["volume"]),
        )
    return IntrabarData(minutes=list(forming.keys()), forming=dict(forming), completed=completed)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_intrabar_replay.py -k loader -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/backtest/intrabar_replay.py tests/test_intrabar_replay.py
git commit -m "feat: intrabar CSV loader (forming + derived completed bars)"
```

---

## Task 5: Replay driver

**Files:**
- Modify: `app/backtest/intrabar_replay.py`
- Modify: `tests/test_intrabar_replay.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_intrabar_replay.py`:

```python
from app.backtest.intrabar_replay import run_intrabar_backtest
from app.backtest.runner import BacktestConfig, BacktestResult
from app.bot_config import StrategyParams


def _flat_data(n_minutes=30):
    """Flat market: no signals. Validates the loop runs end to end."""
    minutes, forming, completed = [], {}, {}
    base = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
    for i in range(n_minutes):
        ts = base + timedelta(minutes=i)
        bm = ts.isoformat()
        minutes.append(bm)
        bar = _bar(ts, 100, 100.2, 99.8, 100)
        forming[bm] = [bar, bar]   # two 5s samples
        completed[bm] = bar
    from app.backtest.intrabar_replay import IntrabarData
    return IntrabarData(minutes=minutes, forming=forming, completed=completed)


def _cfg():
    return BacktestConfig(
        instrument="MGC", bars=iter([]),
        strategy_params=StrategyParams(trend_ema_period=0, vp_enabled=False),
        slippage_ticks_market=0, commission_per_side=Decimal("0"),
    )


def test_driver_runs_and_returns_result():
    data = _flat_data(30)
    result = asyncio.run(run_intrabar_backtest(_cfg(), data))
    assert isinstance(result, BacktestResult)
    assert result.bars_processed == 30
    assert result.stats.trades == 0   # flat market, no signals


def test_driver_is_deterministic():
    data1, data2 = _flat_data(20), _flat_data(20)
    r1 = asyncio.run(run_intrabar_backtest(_cfg(), data1))
    r2 = asyncio.run(run_intrabar_backtest(_cfg(), data2))
    assert r1.stats.net_pnl == r2.stats.net_pnl
    assert r1.stats.trades == r2.stats.trades
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_intrabar_replay.py -k driver -v`
Expected: FAIL — `ImportError: cannot import name 'run_intrabar_backtest'`.

- [ ] **Step 3: Implement the driver**

Append to `app/backtest/intrabar_replay.py`:

```python
def _kz_for_fill(order_killzones: dict[str, str], broker_order_id: str | None) -> str:
    """Map an exit fill's order id back to its entry killzone. Exit ids end
    with -X/-P/-S/-T; strip to recover the entry order id."""
    if not broker_order_id:
        return "unknown"
    bid = broker_order_id
    if len(bid) > 2 and bid[-2] == "-" and bid[-1] in "XPST":
        bid = bid[:-2]
    return order_killzones.get(bid, "unknown")


async def run_intrabar_backtest(cfg: BacktestConfig, data: IntrabarData) -> BacktestResult:
    """Replay 5s samples: resolve open brackets per sample, evaluate forming
    entries per sample (via the engine's shared forming path), and advance
    strategy state on the completed bar at each minute close."""
    broker = PaperBroker(
        starting_balance=cfg.starting_balance,
        slippage_ticks_market=cfg.slippage_ticks_market,
        commission_per_side=cfg.commission_per_side,
        partial_profit_r=cfg.partial_profit_r,
    )
    risk_state = RiskState(config=fifty_k_combine(soft_buffer=cfg.soft_buffer))
    runner = _build_runner(cfg)

    fills_captured: list[dict] = []
    rejected_signals = 0
    order_killzones: dict[str, str] = {}

    async def on_signal(signal: Signal, outcome: OrderOutcome) -> None:
        nonlocal rejected_signals
        if not outcome.placed:
            rejected_signals += 1
            return
        if outcome.broker_order_id:
            order_killzones[outcome.broker_order_id] = signal.killzone

    async def on_fill(fill: Fill) -> None:
        fills_captured.append({
            "ts": fill.ts.isoformat(),
            "instrument": fill.instrument,
            "side": fill.side,
            "fill_price": str(fill.fill_price),
            "size": fill.size,
            "is_entry": fill.is_entry,
            "realized_pnl_delta": str(fill.realized_pnl_delta),
            "killzone": _kz_for_fill(order_killzones, fill.broker_order_id),
        })

    engine = ExecutionEngine(
        broker=broker, risk_state=risk_state, runners=[runner],
        on_signal=on_signal, replay_mode=True,
        contracts=cfg.contracts, risk_per_trade_pct=cfg.risk_per_trade_pct,
        strategy_cfg=cfg.strategy_params,
    )
    broker.on_fill(on_fill)
    await broker.connect()
    await engine.start()

    instrument = cfg.instrument
    for bm in data.minutes:
        for fb in data.forming[bm]:
            await broker.resolve_sample(fb)                       # 1) resolve open brackets
            await engine.evaluate_forming_bar(instrument, runner, fb)  # 2) maybe enter
        # minute close: advance strategy state (and any completed-bar signal).
        # Direct call (not broker.inject_bar) so fills resolve ONLY on samples.
        await engine._handle_bar(data.completed[bm])

    await engine.stop()
    await broker.disconnect()

    stats = _compute_stats(fills_captured, risk_state, cfg.starting_balance)
    trades = _reconstruct_trades(fills_captured)
    return BacktestResult(
        config=cfg, stats=stats, trades=trades,
        bars_processed=len(data.minutes), rejected_signals=rejected_signals,
        label=cfg.label or "intrabar",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_intrabar_replay.py -k driver -v`
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add app/backtest/intrabar_replay.py tests/test_intrabar_replay.py
git commit -m "feat: intrabar replay driver (sample fills + forming entries)"
```

---

## Task 6: Partials-across-samples + same-minute entry/exit tests

**Files:**
- Modify: `tests/test_intrabar_replay.py`

These assert the fidelity gains the spec calls out. They use `resolve_sample` directly (no synthetic strategy needed).

- [ ] **Step 1: Write the tests**

Append to `tests/test_intrabar_replay.py`:

```python
def test_partial_then_be_across_samples():
    """Partial target on one sample -> partial fill + stop to BE; final target
    on a later sample -> remainder exit. Stop size tracks remaining position."""
    broker = PaperBroker(slippage_ticks_market=0, commission_per_side=Decimal("0"),
                         partial_profit_r=Decimal("1.0"))
    fills = []
    async def cap(f): fills.append(f)
    async def go():
        broker.on_fill(cap)
        await broker.connect()
        # entry 100, stop 99 -> 1R=1, partial target=101, final target=102, size 2
        await broker.place_bracket("MGC", "long", 2, Decimal("100"), Decimal("99"), Decimal("102"))
        await broker.resolve_sample(_bar(_ts(5), 100, 101.0, 100, 100.8))   # partial @101
        await broker.resolve_sample(_bar(_ts(10), 100.8, 102.0, 100.7, 102))  # final @102
    asyncio.run(go())
    exits = [f for f in fills if not f.is_entry]
    assert len(exits) == 2
    assert exits[0].fill_price == Decimal("101") and exits[0].size == 1
    assert exits[1].fill_price == Decimal("102") and exits[1].size == 1


def test_no_false_exit_before_entry_same_minute():
    """A bracket placed AFTER an adverse sample is not retro-stopped by it —
    resolution only sees samples that arrive after the entry."""
    broker = PaperBroker(slippage_ticks_market=0, commission_per_side=Decimal("0"))
    fills = []
    async def cap(f): fills.append(f)
    async def go():
        broker.on_fill(cap)
        await broker.connect()
        # Adverse sample BEFORE entry — must not affect a not-yet-open bracket.
        await broker.resolve_sample(_bar(_ts(5), 100, 100.2, 98.0, 99.0))
        await broker.place_bracket("MGC", "long", 1, Decimal("100"), Decimal("99"), Decimal("102"))
        # Benign sample after entry.
        await broker.resolve_sample(_bar(_ts(10), 100, 100.5, 99.8, 100.2))
    asyncio.run(go())
    exits = [f for f in fills if not f.is_entry]
    assert len(exits) == 0   # no stop fill from the pre-entry sample
```

- [ ] **Step 2: Run the tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_intrabar_replay.py -k "partial_then_be or no_false_exit" -v`
Expected: both PASS (these exercise existing `resolve_sample` + `_resolve_bracket`; no new code).

- [ ] **Step 3: Commit**

```bash
git add tests/test_intrabar_replay.py
git commit -m "test: partials-across-samples + same-minute entry/exit fidelity"
```

---

## Task 7: Validation script + acceptance run

**Files:**
- Create: `scripts/validate_intrabar_replay.py`

- [ ] **Step 1: Create the validation script**

Create `scripts/validate_intrabar_replay.py`:

```python
"""
Validate the intrabar replay against captured live data.

Runs run_intrabar_backtest on an intrabar CSV and prints the signals it
produces (count, side, entry minute) so they can be diffed against the live
log for that session (count + character match, +/-1 tolerance).

Usage:
    python scripts/validate_intrabar_replay.py --intrabar intrabar_MGC.csv --trend-ema 0 --vp-off
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.intrabar_replay import load_intrabar_csv, run_intrabar_backtest
from app.backtest.runner import BacktestConfig
from app.bot_config import load_bot_config


async def _run(args: argparse.Namespace) -> None:
    bot = load_bot_config(Path(args.config))
    strat = bot.strategy.model_copy(update={
        "trend_ema_period": args.trend_ema,
        "vp_enabled": (bot.strategy.vp_enabled and not args.vp_off),
    })
    data = load_intrabar_csv(Path(args.intrabar), instrument=args.instrument)
    cfg = BacktestConfig(
        instrument=args.instrument, bars=iter([]),
        strategy_params=strat, enabled_killzones=bot.enabled_killzones,
        contracts=bot.contracts, risk_per_trade_pct=bot.risk_per_trade_pct,
        partial_profit_r=bot.partial_profit_r,
        slippage_ticks_market=1, commission_per_side=Decimal("0.74"),
    )
    result = await run_intrabar_backtest(cfg, data)
    print(f"Window: {data.minutes[0]} .. {data.minutes[-1]}  ({len(data.minutes)} min)")
    print(f"trend_ema={args.trend_ema}  vp_off={args.vp_off}")
    print(f"Signals placed: {len([t for t in result.trades])}  "
          f"rejected: {result.rejected_signals}")
    print(f"Net P&L: {result.stats.net_pnl}  trades: {result.stats.trades}  "
          f"win_rate: {result.stats.win_rate}%")
    for t in result.trades:
        print(f"  {t['entry_ts']}  {t['side']:5} entry={t['entry_price']} "
              f"exit={t['exit_price']} pnl={t['realized_pnl']}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--intrabar", default="intrabar_MGC.csv")
    p.add_argument("--instrument", default="MGC")
    p.add_argument("--config", default="bot_config.json")
    p.add_argument("--trend-ema", type=int, default=0)
    p.add_argument("--vp-off", action="store_true")
    import logging
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(_run(p.parse_args()))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the acceptance check**

Run: `.venv\Scripts\python.exe scripts/validate_intrabar_replay.py --intrabar intrabar_MGC.csv --trend-ema 50 --vp-off`
Expected: completes; prints a signal count. **Acceptance (count+character):** the placed-signal count is within ±1 of the live log for that session (today: ~1 placed at `trend_ema=50`) and the rate is realistic (not 0.3/day). Then run again with `--trend-ema 0` and confirm more signals appear (the EMA-blocked ones). If the count is wildly off (e.g. 0 or 50), investigate the loop ordering/dedup before proceeding.

- [ ] **Step 3: Commit**

```bash
git add scripts/validate_intrabar_replay.py
git commit -m "feat: intrabar replay validation script"
```

---

## Task 8: Full regression

- [ ] **Step 1: Run the suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q --ignore=tests/test_backtest.py --deselect tests/test_main.py::test_paper_mode_full_run`
Expected: no NEW failures vs the known baseline (pre-existing: 6 analytics UnicodeDecodeError, `test_signal_denied_when_already_at_max_contracts`, 1 api backpressure, 2 reconciler balance-drift — all confirmed pre-existing). `tests/test_intrabar_replay.py` fully green.

- [ ] **Step 2: Commit any fixes, then stop**

```bash
git add -A && git commit -m "test: forming-bar replay full regression green"
```

---

## Self-Review

**Spec coverage:**
- [x] Input contract (intrabar CSV, completed = last sample/min) → Task 4
- [x] Approach A: driver + PaperBroker sample-fill, live path behavior-preserved → Tasks 1, 2, 3, 5
- [x] Reuse engine for gate/sizing/VP (no duplicated detection) → Task 1 `evaluate_forming_bar`; Task 5 driver
- [x] Replay loop: resolve-then-enter per sample, on_bar at minute close → Task 5
- [x] Dedup per b2.ts → Task 1 (reuses `_forming_signal_fired`)
- [x] Sample fill model incl. partials, shared `_resolve_bracket` → Tasks 2, 3
- [x] Same-minute entry+exit correctness → Task 6
- [x] Validation harness + success criteria → Task 7
- [x] Testing (loader, resolve_sample, partials, same-minute, dedup, driver smoke, determinism) → Tasks 3, 5, 6 (+ dedup covered by Task 1 reuse + Task 7 acceptance)
- [x] No regression to completed-bar backtest → Tasks 2, 8

**Placeholder scan:** none — every code step is complete. Task 7 acceptance depends on the captured `intrabar_MGC.csv` (documented, not a unit test).

**Type consistency:**
- `IntrabarData(minutes, forming, completed)` — defined Task 4, consumed Task 5.
- `load_intrabar_csv(path, instrument, timeframe) -> IntrabarData` — Task 4, called Tasks 5/7.
- `run_intrabar_backtest(cfg: BacktestConfig, data: IntrabarData) -> BacktestResult` — Task 5, called Task 7.
- `evaluate_forming_bar(self, instrument, runner, forming_bar)` — Task 1, called Task 5 + the poll.
- `_resolve_bracket(self, bracket, high, low, ts)` / `resolve_sample(self, sample)` — Tasks 2/3, called Task 5.
- `_build_runner`, `_compute_stats`, `_reconstruct_trades`, `BacktestConfig`, `BacktestResult` — existing in `app/backtest/runner.py`, imported in Task 4/5.

**Dedup test note:** the per-`b2.ts` dedup is exercised indirectly (Task 1 reuse + Task 7 acceptance). If a unit test is wanted, add one feeding two consecutive identical forming samples to a primed fake runner and asserting one placement — but the fake-runner setup in Task 1 already covers the single-fire path.
