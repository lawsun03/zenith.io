# Phase 2: Partial Profits + BE-Trail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow the backtester to simulate "take half off at 1R, move stop to break-even on the remainder." This gives the walk-forward optimizer a new axis to explore: does partial-profit scaling improve combine pass-rate vs riding the full target?

**Scope (backtest-only):** Partial profit logic lives entirely inside `PaperBroker` and `BacktestConfig`. Live trading with TopstepX requires order-modification flows not yet implemented — this plan does NOT touch `TopstepXBroker` or `StrategyParams`/frontend.

**Architecture:** Add three fields to `_OpenBracket` (`partial_target`, `partial_size`, `partial_filled`). Add `partial_profit_r: Decimal = Decimal("0")` to `PaperBroker.__init__` (0 = disabled). In `place_bracket`, compute partial target = `entry ± R × partial_profit_r` when enabled. In `inject_bar`, check partial target BEFORE the main stop/target check: emit a partial fill, shrink the bracket size, and move the stop to break-even in-place. Add `_close_partial()` helper for the partial fill. Wire `partial_profit_r` through `BacktestConfig` and expose as a `--partial-profit-r` CLI arg in `scripts/walkforward.py`.

**Tech Stack:** Python dataclasses (mutable), Decimal arithmetic, existing PaperBroker fill pipeline.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `app/broker/paper.py` | `_OpenBracket` partial fields; PaperBroker ctor arg; `place_bracket` computes partial target; `inject_bar` checks + acts on partial; `_close_partial` method |
| Modify | `app/backtest/runner.py` | `BacktestConfig.partial_profit_r`; pass to PaperBroker in `run_backtest` |
| Modify | `scripts/walkforward.py` | `--partial-profit-r` CLI arg wired to `BacktestConfig` |
| Create | `tests/test_partial_profit.py` | Unit tests for partial fill logic |

---

## Task 1: PaperBroker Partial-Profit Engine

**Files:**
- Modify: `app/broker/paper.py`
- Create: `tests/test_partial_profit.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_partial_profit.py`:

```python
"""Tests for partial-profit + BE-trail in PaperBroker."""
import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from app.broker.paper import PaperBroker
from app.broker.events import Bar, Fill


def _bar(ts, o, h, l, c, instrument="MGC"):
    return Bar(
        instrument=instrument, timeframe="1min", ts=ts,
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)), volume=100,
    )


def _now(offset_min: int = 0):
    return datetime(2026, 1, 2, 10, offset_min, tzinfo=timezone.utc)


async def _run(broker, bars, side="long", entry=100, stop=99, target=102, instrument="MGC"):
    fills = []

    async def collect(f: Fill):
        fills.append(f)

    broker.on_fill(collect)
    await broker.connect()
    await broker.place_bracket(instrument, side, 2, Decimal(str(entry)),
                                Decimal(str(stop)), Decimal(str(target)))
    for b in bars:
        await broker.inject_bar(b)
    return fills


def test_no_partial_baseline():
    """With partial_profit_r=0 (disabled), size=2 fills at full target."""
    broker = PaperBroker(
        starting_balance=Decimal("50000"),
        slippage_ticks_market=0,
        commission_per_side=Decimal("0"),
        partial_profit_r=Decimal("0"),
    )
    fills = asyncio.run(_run(
        broker,
        [_bar(_now(1), 100, 103, 99.5, 102)],  # high hits target 102
    ))
    exits = [f for f in fills if not f.is_entry]
    assert len(exits) == 1
    assert exits[0].size == 2
    assert exits[0].fill_price == Decimal("102")


def test_partial_at_1r_long():
    """partial_profit_r=1.0: take 1 contract at 1R (101), stop moves to BE (100)."""
    broker = PaperBroker(
        starting_balance=Decimal("50000"),
        slippage_ticks_market=0,
        commission_per_side=Decimal("0"),
        partial_profit_r=Decimal("1.0"),
    )
    # Entry=100, stop=99, so 1R=1. Partial target = 101. Final target = 102.
    # Bar hits 101 but not 102 — only partial fill.
    fills = asyncio.run(_run(
        broker,
        [_bar(_now(1), 100, 101, 99.5, 100.5)],
    ))
    exits = [f for f in fills if not f.is_entry]
    assert len(exits) == 1, f"Expected 1 partial exit, got {len(exits)}"
    assert exits[0].size == 1, "Partial fill should be 1 contract"
    assert exits[0].fill_price == Decimal("101")


def test_be_stop_protects_remainder():
    """After partial at 1R, stop moves to BE so remainder exits at entry on drawdown."""
    broker = PaperBroker(
        starting_balance=Decimal("50000"),
        slippage_ticks_market=0,
        commission_per_side=Decimal("0"),
        partial_profit_r=Decimal("1.0"),
    )
    # Bar 1: high=101 triggers partial. Low=99.5 — original stop was 99, not hit,
    #   but BE stop is now 100 and 99.5 < 100, so remainder exits at BE=100.
    fills = asyncio.run(_run(
        broker,
        [_bar(_now(1), 100, 101, 99.5, 100)],
    ))
    exits = [f for f in fills if not f.is_entry]
    assert len(exits) == 2, f"Expected partial + BE exit, got {len(exits)}"
    partial_exit = exits[0]
    be_exit = exits[1]
    assert partial_exit.size == 1
    assert partial_exit.fill_price == Decimal("101")
    assert be_exit.size == 1
    assert be_exit.fill_price == Decimal("100")  # exit at BE, not original stop (99)


def test_full_target_after_partial():
    """Bar 1 triggers partial. Bar 2 hits full target; remainder exits there."""
    broker = PaperBroker(
        starting_balance=Decimal("50000"),
        slippage_ticks_market=0,
        commission_per_side=Decimal("0"),
        partial_profit_r=Decimal("1.0"),
    )
    fills = asyncio.run(_run(
        broker,
        [
            _bar(_now(1), 100, 101, 100, 100.5),   # partial at 101
            _bar(_now(2), 100.5, 103, 100, 102),   # full target at 102
        ],
    ))
    exits = [f for f in fills if not f.is_entry]
    assert len(exits) == 2
    assert exits[0].fill_price == Decimal("101")  # partial
    assert exits[1].fill_price == Decimal("102")  # full target
    assert exits[0].size == 1
    assert exits[1].size == 1


def test_stop_before_partial():
    """If stop is hit before partial target, full exit at original stop (no partial)."""
    broker = PaperBroker(
        starting_balance=Decimal("50000"),
        slippage_ticks_market=0,
        commission_per_side=Decimal("0"),
        partial_profit_r=Decimal("1.0"),
    )
    fills = asyncio.run(_run(
        broker,
        [_bar(_now(1), 100, 100.5, 98.5, 99)],  # low hits stop 99
    ))
    exits = [f for f in fills if not f.is_entry]
    assert len(exits) == 1
    assert exits[0].size == 2  # full exit, no partial taken
    assert exits[0].fill_price == Decimal("99")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv\Scripts\python.exe -m pytest tests/test_partial_profit.py -v
```

Expected: `TypeError: __init__() got an unexpected keyword argument 'partial_profit_r'`

- [ ] **Step 3: Add fields to `_OpenBracket`**

In `app/broker/paper.py`, replace the `_OpenBracket` dataclass with:

```python
@dataclass
class _OpenBracket:
    """Internal record of a live bracket position."""

    order_id: str
    instrument: str
    side: Side
    size: int
    entry: Decimal
    stop: Decimal
    target: Decimal
    # Partial profit state (all 0/None/False = disabled)
    partial_target: Decimal | None = None  # price to take partial profit
    partial_size: int = 0                  # contracts to exit at partial_target
    partial_filled: bool = False           # True once the partial fill has been emitted
```

- [ ] **Step 4: Add `partial_profit_r` to `PaperBroker.__init__`**

In `PaperBroker.__init__`, add the parameter (after `commission_per_side`):

```python
def __init__(
    self,
    starting_balance: Decimal = Decimal("50000"),
    pessimistic_whipsaw: bool = True,
    slippage_ticks_market: int = 1,
    commission_per_side: Decimal | None = None,
    partial_profit_r: Decimal = Decimal("0"),  # 0 = disabled; 1.0 = take half at 1R
) -> None:
    self._starting_balance = starting_balance
    self._balance = starting_balance
    self._pessimistic = pessimistic_whipsaw
    self._slippage_ticks_market = slippage_ticks_market
    self._commission_per_side = commission_per_side
    self._partial_profit_r = partial_profit_r
    self._connected = False
    # ... rest unchanged
```

- [ ] **Step 5: Compute partial target in `place_bracket`**

In `place_bracket`, after constructing `bracket = _OpenBracket(...)` and before `self._open[order_id] = bracket`, add:

```python
        if self._partial_profit_r > 0 and size >= 2:
            r = abs(slipped_entry - stop)
            if side == "long":
                pt = slipped_entry + r * self._partial_profit_r
            else:
                pt = slipped_entry - r * self._partial_profit_r
            bracket.partial_target = pt
            bracket.partial_size = size // 2
```

- [ ] **Step 6: Add `_close_partial()` helper**

Add this method to `PaperBroker` (place it immediately before `_close_bracket`):

```python
async def _close_partial(self, bracket: _OpenBracket, exit_price: Decimal, ts: datetime) -> None:
    """Emit a partial fill for bracket.partial_size contracts. Does NOT remove bracket."""
    size = bracket.partial_size
    ticks_per_point = self._ticks_per_point(bracket.instrument)
    points = (exit_price - bracket.entry) if bracket.side == "long" else (bracket.entry - exit_price)
    pnl = points * ticks_per_point * _tick_value(bracket.instrument) * size
    commission = self._commission_for(bracket.instrument)
    pnl -= commission * size
    self._balance += pnl

    await self._fanout(
        self._fill_handlers,
        Fill(
            ts=ts,
            instrument=bracket.instrument,
            side="short" if bracket.side == "long" else "long",
            fill_price=exit_price,
            size=size,
            is_entry=False,
            realized_pnl_delta=pnl,
            contracts_delta=-size if bracket.side == "long" else size,
            broker_order_id=f"{bracket.order_id}-P",
            is_stop=False,
        ),
    )
    log.info(
        "Partial fill: %s %d @ %s P&L=%s; stop moved to BE=%s",
        bracket.instrument, size, exit_price, pnl, bracket.entry,
    )
```

- [ ] **Step 7: Check partial target in `inject_bar`**

In `inject_bar`, in the `for oid in list(self._open.keys()):` loop, add a partial-target check at the very beginning (before the existing `stop_hit` / `target_hit` block):

```python
            # Partial profit: if partial_target touched and not yet filled,
            # close partial_size contracts and move the stop to break-even.
            if (
                not bracket.partial_filled
                and bracket.partial_target is not None
                and bracket.partial_size > 0
            ):
                partial_hit = (
                    (bracket.side == "long" and bar.high >= bracket.partial_target)
                    or (bracket.side == "short" and bar.low <= bracket.partial_target)
                )
                if partial_hit:
                    await self._close_partial(bracket, bracket.partial_target, bar.ts)
                    bracket.size -= bracket.partial_size
                    bracket.stop = bracket.entry  # move stop to break-even
                    bracket.partial_filled = True
                    # bracket is mutated in-place; self._open[oid] references same object
```

This block must appear BEFORE the `stop_hit = ...` computation so that the updated `bracket.stop` (now at BE) is used in the subsequent stop check.

- [ ] **Step 8: Run tests**

```bash
.venv\Scripts\python.exe -m pytest tests/test_partial_profit.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 9: Run full suite**

```bash
.venv\Scripts\python.exe -m pytest tests/ --ignore=tests/test_backtest.py -q
```

Expected: no new failures beyond the known 5 pre-existing ones.

- [ ] **Step 10: Commit**

```bash
git add app/broker/paper.py tests/test_partial_profit.py
git commit -m "feat: partial profit + BE-trail in PaperBroker (backtest only)"
```

---

## Task 2: Wire Through BacktestConfig and Walkforward CLI

**Files:**
- Modify: `app/backtest/runner.py`
- Modify: `scripts/walkforward.py`

- [ ] **Step 1: Add `partial_profit_r` to `BacktestConfig`**

In `app/backtest/runner.py`, add to the `BacktestConfig` dataclass (after `commission_per_side`):

```python
@dataclass
class BacktestConfig:
    instrument: str
    bars: Iterator[Bar]
    composer_config: ComposerConfig
    starting_balance: Decimal = field(default_factory=lambda: Decimal("50000"))
    soft_buffer: Decimal = field(default_factory=lambda: Decimal("500"))
    liquidity_config: LiquidityConfig = field(default_factory=LiquidityConfig)
    displacement_config: DisplacementConfig = field(default_factory=DisplacementConfig)
    enabled_killzones: list[str] | None = None
    timeframe: str = "1min"
    contracts: int = 1
    slippage_ticks_market: int = 1
    commission_per_side: Decimal = field(default_factory=lambda: Decimal("0.74"))
    partial_profit_r: Decimal = field(default_factory=lambda: Decimal("0"))  # 0 = disabled
    label: str = ""
```

- [ ] **Step 2: Pass `partial_profit_r` to PaperBroker in `run_backtest`**

In `run_backtest`, update the `PaperBroker(...)` constructor call:

```python
    broker = PaperBroker(
        starting_balance=cfg.starting_balance,
        slippage_ticks_market=cfg.slippage_ticks_market,
        commission_per_side=cfg.commission_per_side,
        partial_profit_r=cfg.partial_profit_r,
    )
```

- [ ] **Step 3: Add `--partial-profit-r` to `scripts/walkforward.py`**

In `scripts/walkforward.py`, add to the argparse block in `main()` (after `--step-days`):

```python
    parser.add_argument(
        "--partial-profit-r", type=Decimal, default=Decimal("0"),
        help="Partial-profit R-multiple (0 = disabled, 1.0 = take half at 1R then BE-trail)",
    )
```

In `_run()`, pass it into the `BacktestConfig`:

```python
    base = BacktestConfig(
        instrument=instrument,
        bars=iter([]),
        starting_balance=Decimal("50000"),
        soft_buffer=Decimal("500"),
        liquidity_config=LiquidityConfig(),
        displacement_config=DisplacementConfig(),
        composer_config=ComposerConfig(instrument=instrument),
        slippage_ticks_market=1,
        commission_per_side=Decimal("0.74"),
        partial_profit_r=args.partial_profit_r,
    )
```

- [ ] **Step 4: Run smoke test**

```bash
.venv\Scripts\python.exe scripts/walkforward.py --help
```

Expected: `--partial-profit-r` appears in the output.

- [ ] **Step 5: Run full test suite**

```bash
.venv\Scripts\python.exe -m pytest tests/ --ignore=tests/test_backtest.py -q
```

Expected: no new failures.

- [ ] **Step 6: Commit**

```bash
git add app/backtest/runner.py scripts/walkforward.py
git commit -m "feat: partial_profit_r in BacktestConfig and walkforward CLI (--partial-profit-r)"
```

---

## Self-Review

**Spec coverage:**
- [x] `_OpenBracket.partial_target / partial_size / partial_filled` → Task 1, Step 3
- [x] `PaperBroker.__init__` `partial_profit_r` → Task 1, Step 4
- [x] `place_bracket` computes partial price at 1R × `partial_profit_r` → Task 1, Step 5
- [x] `_close_partial()` emits Fill, updates balance, does NOT remove bracket → Task 1, Step 6
- [x] `inject_bar` checks partial BEFORE main stop/target → Task 1, Step 7
- [x] After partial: bracket.size reduced, bracket.stop = entry (BE), partial_filled = True → Task 1, Step 7
- [x] `BacktestConfig.partial_profit_r` → Task 2, Step 1
- [x] `run_backtest` passes it to PaperBroker → Task 2, Step 2
- [x] `scripts/walkforward.py` `--partial-profit-r` CLI arg → Task 2, Step 3

**Placeholder scan:** None.

**Type consistency:**
- `partial_profit_r: Decimal = Decimal("0")` — consistent with `min_atr_filter`, `max_atr_filter` convention (0 = disabled)
- `broker_order_id=f"{bracket.order_id}-P"` — distinct from `-S` (stop) and `-T` (target) and `-X` (full close)
- `_close_partial` computes P&L using the same `_ticks_per_point × _tick_value` chain as `_close_bracket`
- `bracket.size -= bracket.partial_size` — `size` was the total, `partial_size = size // 2`. Remaining = 1 contract when starting with 2. `contracts_delta` in partial Fill uses `partial_size`, not `bracket.size`.

**Edge case: `size=1`**
- `partial_size = 1 // 2 = 0` — the `if bracket.partial_size > 0` guard prevents the partial logic from firing. Single-contract trades get no partial profit. This is correct.
