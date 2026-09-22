# MFE/MAE Excursion Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **RESUME CONTEXT (read first):** This plan is self-contained. Background: the bot records placed trades to `trades/trades.csv` (with grade/slippage) and rejected setups to `trades/rejections.csv` (engine `vp_filter`/`htf_bias` denials + runner `grader_*`/`premature_liquidity`/`invalidated`). This plan adds **excursion tracking** so we can tell, for each trade AND rejection, how far price moved in favor/against afterward. See memory `project-monitoring-insights-backlog` (Stage-1 item #3) and `reference-entry-slippage-mechanism`.

**Goal:** For every placed trade and every rejection, record **MFE** (max favorable excursion) and **MAE** (max adverse excursion) over the next N bars from the (would-be) entry, plus whether price reached the (would-be) target — written to `trades/excursions.csv`. Surfaces too-tight stops (trades with high MFE that still stopped) and profitable misses (rejections whose MFE reached target).

**Architecture:** A pure, stateful `ExcursionTracker` (no I/O) maintains open "windows", each anchored at a ref price + side + target with a countdown of bars. Each bar updates running max-high/min-low; when the countdown hits 0 the window completes and an `emit` callback writes one `excursions.csv` row. `main.py` opens windows from the fill journaler (trades, keyed by broker_order_id) and the reject paths (rejections), and feeds bars via `broker.on_bar`. Decoupled CSV (separate from trades/rejections) because MFE/MAE is only known N bars *after* the event; `/analyze-trades` joins on `key`. Pure observation — never touches orders.

**Tech Stack:** Python 3, dataclasses, `csv` stdlib, pytest.

**Config:** `_MFE_WINDOW_BARS = 30` constant in `main.py` (30 min on 1-min bars); tunable later.

---

## File Structure

- **Create** `app/execution/excursion.py` — `ExcursionWindow` dataclass + `ExcursionTracker` (open/on_bar/pure math). No I/O.
- **Create** `tests/test_excursion.py` — unit tests for the MFE/MAE math + window lifecycle.
- **Modify** `app/main.py` — `_EXCURSIONS_CSV`, `_EXCURSIONS_HEADERS`, `_daily_excursions_path()`, `_append_excursion_csv(...)`; construct one `ExcursionTracker`; open trade windows in the fill journaler, rejection windows in the reject paths; subscribe `tracker.on_bar` to the broker; `_MFE_WINDOW_BARS`.
- **Modify** `tests/test_main.py` — test the excursion writer.

---

### Task 1: ExcursionTracker + MFE/MAE math (pure, no I/O)

**Files:** Create `app/execution/excursion.py`, `tests/test_excursion.py`

- [ ] **Step 1: Write failing tests** (`tests/test_excursion.py`)

```python
from decimal import Decimal
from datetime import datetime, timezone
from app.sim.events import Bar
from app.execution.excursion import ExcursionTracker, ExcursionWindow


def _bar(o, h, l, c, ts=None):
    return Bar(instrument="MGC", timeframe="1min",
               ts=ts or datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
               open=Decimal(o), high=Decimal(h), low=Decimal(l),
               close=Decimal(c), volume=Decimal("1"))


def test_long_excursion_mfe_mae_and_target():
    emitted = []
    t = ExcursionTracker(emit=emitted.append)
    # LONG ref=100, target=104, window=2 bars
    t.open(key="K1", kind="trade", side="long", ref=Decimal("100"),
           target=Decimal("104"), window_bars=2)
    t.on_bar(_bar("100", "103", "99", "102"))   # high 103 (+3), low 99 (-1)
    assert not emitted                          # window not done yet
    t.on_bar(_bar("102", "104.5", "101", "104")) # high 104.5 (+4.5), reaches target
    assert len(emitted) == 1
    w = emitted[0]
    assert w.mfe == Decimal("4.5")   # max(103,104.5) - 100
    assert w.mae == Decimal("1")     # 100 - min(99,101)
    assert w.reached_target is True  # high 104.5 >= 104


def test_short_excursion_mfe_mae():
    emitted = []
    t = ExcursionTracker(emit=emitted.append)
    t.open(key="K2", kind="rejection", side="short", ref=Decimal("100"),
           target=Decimal("96"), window_bars=1)
    t.on_bar(_bar("100", "101", "97", "98"))  # favorable: 100-97=3, adverse: 101-100=1
    assert len(emitted) == 1
    w = emitted[0]
    assert w.mfe == Decimal("3")             # ref - min_low
    assert w.mae == Decimal("1")             # max_high - ref
    assert w.reached_target is False         # low 97 > target 96


def test_multiple_windows_independent():
    emitted = []
    t = ExcursionTracker(emit=emitted.append)
    t.open(key="A", kind="trade", side="long", ref=Decimal("100"), target=None, window_bars=1)
    t.open(key="B", kind="trade", side="short", ref=Decimal("200"), target=None, window_bars=2)
    t.on_bar(_bar("100", "101", "99", "100"))
    assert [w.key for w in emitted] == ["A"]   # only A completes
    t.on_bar(_bar("200", "201", "198", "199"))
    assert [w.key for w in emitted] == ["A", "B"]
```

- [ ] **Step 2: Run to verify FAIL** — `.venv/Scripts/python.exe -m pytest tests/test_excursion.py -v` → ImportError.

- [ ] **Step 3: Implement** `app/execution/excursion.py`

```python
"""Excursion tracker — records MFE/MAE over N bars after a trade or rejection.

Pure + stateful: no I/O. open() registers a window; on_bar() advances every
window and calls emit(window) when its bar countdown completes. Used for
observability only — never affects orders."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Literal

from app.sim.events import Bar


@dataclass
class ExcursionWindow:
    key: str
    kind: Literal["trade", "rejection"]
    side: str                       # "long" | "short"
    ref: Decimal                    # (would-be) entry price
    target: Decimal | None
    bars_left: int
    max_high: Decimal               # running max of bar.high while open
    min_low: Decimal                # running min of bar.low while open
    reached_target: bool = False

    @property
    def mfe(self) -> Decimal:
        """Max favorable excursion in points (positive = in your favor)."""
        return (self.max_high - self.ref) if self.side == "long" else (self.ref - self.min_low)

    @property
    def mae(self) -> Decimal:
        """Max adverse excursion in points (positive = against you)."""
        return (self.ref - self.min_low) if self.side == "long" else (self.max_high - self.ref)


class ExcursionTracker:
    def __init__(self, emit: Callable[[ExcursionWindow], None]) -> None:
        self._emit = emit
        self._windows: list[ExcursionWindow] = []

    def open(self, *, key: str, kind: str, side: str, ref: Decimal,
             target: Decimal | None, window_bars: int) -> None:
        if window_bars <= 0:
            return
        self._windows.append(ExcursionWindow(
            key=key, kind=kind, side=side, ref=ref, target=target,
            bars_left=window_bars, max_high=ref, min_low=ref,
        ))

    def on_bar(self, bar: Bar) -> None:
        """Advance every open window with this bar; emit + drop completed ones."""
        still_open: list[ExcursionWindow] = []
        for w in self._windows:
            if bar.high > w.max_high:
                w.max_high = bar.high
            if bar.low < w.min_low:
                w.min_low = bar.low
            if w.target is not None and not w.reached_target:
                if (w.side == "long" and bar.high >= w.target) or \
                   (w.side == "short" and bar.low <= w.target):
                    w.reached_target = True
            w.bars_left -= 1
            if w.bars_left <= 0:
                try:
                    self._emit(w)
                except Exception:
                    pass  # observability must never break the bar handler
            else:
                still_open.append(w)
        self._windows = still_open
```

- [ ] **Step 4: Run to verify PASS** — `.venv/Scripts/python.exe -m pytest tests/test_excursion.py -v` → all pass.

- [ ] **Step 5: Commit**

```bash
git add app/execution/excursion.py tests/test_excursion.py
git commit -m "feat: ExcursionTracker - pure MFE/MAE window math"
```

---

### Task 2: excursions.csv writer

**Files:** Modify `app/main.py`; Test `tests/test_main.py`

- [ ] **Step 1: Write failing test** (`tests/test_main.py`)

```python
def test_append_excursion_csv_writes_row(tmp_path: Path, monkeypatch):
    from decimal import Decimal
    from app.main import _append_excursion_csv
    from app.execution.excursion import ExcursionWindow
    master = tmp_path / "excursions.csv"
    daily = tmp_path / "excursions_today.csv"
    monkeypatch.setattr("app.main._EXCURSIONS_CSV", master)
    monkeypatch.setattr("app.main._daily_excursions_path", lambda: daily)
    w = ExcursionWindow(key="OID-1", kind="trade", side="long",
                        ref=Decimal("100"), target=Decimal("104"), bars_left=0,
                        max_high=Decimal("104.5"), min_low=Decimal("99"),
                        reached_target=True)
    _append_excursion_csv(w)
    rows = list(csv.DictReader(master.open(encoding="utf-8")))
    assert rows[0]["key"] == "OID-1"
    assert rows[0]["kind"] == "trade"
    assert rows[0]["mfe"] == "4.5"
    assert rows[0]["mae"] == "1"
    assert rows[0]["reached_target"] == "True"
```

- [ ] **Step 2: Run to verify FAIL** — ImportError.

- [ ] **Step 3: Implement in `app/main.py`** (place near `_append_rejection_csv`)

```python
_EXCURSIONS_CSV = Path("trades/excursions.csv")
_EXCURSIONS_HEADERS = [
    "key", "kind", "side", "ref", "target", "mfe", "mae", "reached_target",
]


def _daily_excursions_path() -> Path:
    ct_date = datetime.now(_CT).strftime("%Y-%m-%d")
    return Path("trades") / f"excursions_{ct_date}.csv"


def _append_excursion_csv(w) -> None:
    """Append one completed excursion window (MFE/MAE over N bars)."""
    row = [w.key, w.kind, w.side, str(w.ref),
           str(w.target) if w.target is not None else "",
           str(w.mfe), str(w.mae), str(w.reached_target)]
    for path in (_EXCURSIONS_CSV, _daily_excursions_path()):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_header = not path.exists()
            with path.open("a", newline="", encoding="utf-8") as f:
                wr = csv.writer(f)
                if write_header:
                    wr.writerow(_EXCURSIONS_HEADERS)
                wr.writerow(row)
        except Exception:
            log.exception("_append_excursion_csv failed for %s", path)
```

- [ ] **Step 4: Run to verify PASS**.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_main.py
git commit -m "feat: excursions.csv writer for MFE/MAE rows"
```

---

### Task 3: Wire the tracker (open windows from trades + rejections; feed bars)

**Files:** Modify `app/main.py`; Test `tests/test_main.py`

- [ ] **Step 1: Write failing test** — the fill journaler opens a trade excursion window.

```python
def test_fill_journaler_opens_trade_excursion(monkeypatch, tmp_path):
    """An ENTRY fill opens an excursion window keyed by broker_order_id."""
    from app.main import _make_fill_journaler
    from app.execution.excursion import ExcursionTracker
    from app.api.journal import Journal
    monkeypatch.setattr("app.main._TRADES_CSV", tmp_path / "t.csv")
    monkeypatch.setattr("app.main._daily_csv_path", lambda: tmp_path / "td.csv")
    monkeypatch.setattr("app.main._pending_signal_meta",
                        {"OID9": {"signal_entry": "100", "target": "104"}})
    opened = []
    tracker = ExcursionTracker(emit=lambda w: None)
    monkeypatch.setattr(tracker, "open", lambda **k: opened.append(k))
    on_fill = _make_fill_journaler(Journal(), excursion_tracker=tracker)
    fill = Fill(ts=datetime(2026, 6, 2, tzinfo=timezone.utc), instrument="MGC",
                side="long", fill_price=Decimal("100.5"), size=20, is_entry=True,
                realized_pnl_delta=Decimal("0"), contracts_delta=20, broker_order_id="OID9")
    asyncio.run(on_fill(fill))
    assert opened and opened[0]["key"] == "OID9"
    assert opened[0]["kind"] == "trade" and opened[0]["side"] == "long"
```

- [ ] **Step 2: Run to verify FAIL** — `_make_fill_journaler` has no `excursion_tracker` param.

- [ ] **Step 3: Implement wiring in `app/main.py`**

(a) Add `_MFE_WINDOW_BARS = 30` near the other module constants.

(b) `_make_fill_journaler(...)`: add param `excursion_tracker=None`. Inside `on_fill`, BEFORE `_append_fill_csv(fill)` (which pops the meta), for ENTRY fills open a window:

```python
        if fill.is_entry and excursion_tracker is not None:
            m = _pending_signal_meta.get(fill.broker_order_id, {})
            tgt = m.get("target")
            excursion_tracker.open(
                key=fill.broker_order_id or fill.instrument, kind="trade",
                side=fill.side, ref=Decimal(str(fill.fill_price)),
                target=Decimal(tgt) if tgt else None, window_bars=_MFE_WINDOW_BARS,
            )
        _append_fill_csv(fill)
```

(c) `_make_reject_journaler()`: add param `excursion_tracker=None`; in `on_reject`, after writing the CSV, open a window:

```python
        if excursion_tracker is not None and info.entry is not None:
            excursion_tracker.open(
                key=f"rej-{instrument}-{info.reason}-{datetime.now(timezone.utc).timestamp():.0f}",
                kind="rejection", side=info.side, ref=info.entry,
                target=info.target, window_bars=_MFE_WINDOW_BARS,
            )
```

(d) In `journal_signal`'s `else` (engine-denial) branch, after `_append_rejection_csv(...)`, open a window (the builder must capture `excursion_tracker`; thread it into `_make_signal_journaler` as a param and store in closure):

```python
            if excursion_tracker is not None:
                excursion_tracker.open(
                    key=f"rej-{signal.instrument}-{outcome.reason}-{datetime.now(timezone.utc).timestamp():.0f}",
                    kind="rejection", side=signal.side, ref=signal.entry,
                    target=signal.target, window_bars=_MFE_WINDOW_BARS,
                )
```

(e) In `_async_main`: construct the tracker and wire it:

```python
    excursion_tracker = ExcursionTracker(emit=_append_excursion_csv)
```
Pass `excursion_tracker=excursion_tracker` into `_make_signal_journaler(...)`, `_make_fill_journaler(...)`, and `_make_reject_journaler(...)`; and subscribe bars:
```python
    broker.on_bar(lambda b: excursion_tracker.on_bar(b))
```
(Add `from app.execution.excursion import ExcursionTracker` to the imports.)

> NOTE: `broker.on_bar` handlers are async in this codebase (see `_make_bar_journaler`). Wrap as an async shim:
> ```python
> async def _excursion_on_bar(b): excursion_tracker.on_bar(b)
> broker.on_bar(_excursion_on_bar)
> ```

- [ ] **Step 4: Run to verify PASS** — the fill-journaler test passes.

- [ ] **Step 5: Run `tests/test_main.py` + `tests/test_excursion.py`** — all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_main.py
git commit -m "feat: wire ExcursionTracker to fills, rejections, and the bar stream"
```

---

### Task 4: Deploy + verify

- [ ] **Step 1:** Restart the practice bot (`run-bot` skill — zombie-safe PID kill + `Tee -Append`).
- [ ] **Step 2:** Confirm startup clean (no import errors): `tr -d '\000' < logs/$(date +%F).log | grep -a "Live mode running" | tail -1`.
- [ ] **Step 3:** After ~30+ min of activity (one window length), check: `head -1 trades/excursions.csv; tail -5 trades/excursions.csv` — expect `kind` in `trade`/`rejection`, numeric `mfe`/`mae`, `reached_target` bool. If quiet, note deferred — unit tests prove the math + wiring.

---

## Self-Review

**Spec coverage:** MFE/MAE for both trades (Task 3b) and rejections (Task 3c engine denials via journal_signal, Task 3c runner via on_reject) — full scope. `reached_target` included. Separate `excursions.csv` keyed by `key` for `/analyze-trades` to join.

**Placeholder scan:** All code concrete. The Task 3 wiring threads `excursion_tracker` through three existing builders — exact insertion points named. The one NOTE (async `on_bar` shim) gives the exact code.

**Type consistency:** `ExcursionWindow.mfe`/`.mae`/`.reached_target` defined in Task 1 are read in Task 2's writer. `ExcursionTracker.open(key, kind, side, ref, target, window_bars)` signature matches all three call sites (Task 3 b/c/d). `_append_excursion_csv(w)` takes one window; `emit=_append_excursion_csv` matches.

**Risk:** Pure observation; `on_bar` does O(open windows) tiny work; `emit` is wrapped in try/except so a CSV error can't break the bar handler. No order interaction.
