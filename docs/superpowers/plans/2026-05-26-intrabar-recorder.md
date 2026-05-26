# Intrabar Recorder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Continuously record a 5-second forming-bar snapshot of MGC to a single rolling `intrabar_MGC.csv` while the bot runs, so trade post-mortems can measure how far a trade moved into profit before exit.

**Architecture:** A background asyncio task in `TopstepXBroker` reads the existing `self._forming_bar` (maintained by the QUOTE_UPDATE handler) every 5 seconds and appends a row to a CSV via a pure module-level helper. It is a pure observer — never touches order flow or the quote hot path. Started at the end of `subscribe()`, cancelled in `disconnect()`.

**Tech Stack:** Python `asyncio`, stdlib `csv`, `pathlib.Path`, existing `Bar` dataclass, pytest.

**Spec:** `docs/superpowers/specs/2026-05-26-intrabar-recorder-design.md`

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `app/broker/topstepx.py` | Module constants (`_INTRABAR_SAMPLE_SECONDS`, `_INTRABAR_HEADERS`); pure helpers `_intrabar_row` + `_append_intrabar_csv`; `self._intrabar_task` init; sampler loop method; start in `subscribe()`; cancel in `disconnect()` |
| Create | `tests/test_intrabar_recorder.py` | Unit tests for row building and CSV append (header-once, append) |

The CSV-writing logic is split into two pure, instance-free module functions so it can be tested with no SDK and no broker instance. The loop method is thin glue (sleep → snapshot → append) and is verified by inspection plus the operational success criteria; its timing is not unit-tested.

---

## Task 1: Pure CSV helpers (`_intrabar_row`, `_append_intrabar_csv`)

**Files:**
- Create: `tests/test_intrabar_recorder.py`
- Modify: `app/broker/topstepx.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_intrabar_recorder.py`:

```python
"""Tests for the intrabar forming-bar snapshot recorder."""
from datetime import datetime, timezone
from decimal import Decimal

from app.broker.events import Bar
from app.broker.topstepx import (
    _INTRABAR_HEADERS,
    _append_intrabar_csv,
    _intrabar_row,
)


def _bar():
    return Bar(
        instrument="MGC",
        timeframe="1min",
        ts=datetime(2026, 5, 26, 13, 4, tzinfo=timezone.utc),
        open=Decimal("4530.0"),
        high=Decimal("4531.5"),
        low=Decimal("4529.5"),
        close=Decimal("4530.8"),
        volume=42,
    )


def _sample_ts():
    return datetime(2026, 5, 26, 13, 4, 35, tzinfo=timezone.utc)


def test_intrabar_row_fields():
    """Row is built in header order, prices as plain strings, full UTC timestamps."""
    row = _intrabar_row(_bar(), _sample_ts())
    assert row == [
        "2026-05-26T13:04:35+00:00",  # sample_ts
        "MGC",                        # instrument
        "2026-05-26T13:04:00+00:00",  # bar_minute (forming bar ts)
        "4530.0",                     # open
        "4531.5",                     # high
        "4529.5",                     # low
        "4530.8",                     # close
        "42",                         # volume
    ]
    assert len(row) == len(_INTRABAR_HEADERS)


def test_append_creates_file_with_header(tmp_path):
    """First append creates the file and writes the header once, then the data row."""
    path = tmp_path / "intrabar_MGC.csv"
    _append_intrabar_csv(_bar(), _sample_ts(), path=path)
    lines = path.read_text().splitlines()
    assert lines[0] == ",".join(_INTRABAR_HEADERS)
    assert len(lines) == 2  # header + one data row
    assert lines[1].startswith("2026-05-26T13:04:35+00:00,MGC,")


def test_append_writes_header_only_once(tmp_path):
    """Appending twice yields one header and two data rows (rolling-file contract)."""
    path = tmp_path / "intrabar_MGC.csv"
    _append_intrabar_csv(_bar(), _sample_ts(), path=path)
    _append_intrabar_csv(_bar(), _sample_ts(), path=path)
    lines = path.read_text().splitlines()
    header_count = sum(1 for ln in lines if ln == ",".join(_INTRABAR_HEADERS))
    assert header_count == 1
    assert len(lines) == 3  # header + two data rows
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_intrabar_recorder.py -v`
Expected: `ImportError` / `cannot import name '_intrabar_row' from 'app.broker.topstepx'`

- [ ] **Step 3: Add stdlib imports**

In `app/broker/topstepx.py`, the import block at the top currently reads:

```python
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable
```

Add `csv` and `Path` so it becomes:

```python
import asyncio
import csv
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable
```

- [ ] **Step 4: Add module constants and pure helpers**

In `app/broker/topstepx.py`, immediately after the `_POINT_VALUE` dict definition (and any related module constants near the top), add:

```python
# Intrabar recorder: snapshot the forming bar this often (seconds) and append
# to a single rolling CSV per instrument. Pure observer — off the order path.
_INTRABAR_SAMPLE_SECONDS = 5
_INTRABAR_HEADERS = [
    "sample_ts", "instrument", "bar_minute",
    "open", "high", "low", "close", "volume",
]


def _intrabar_row(bar: Bar, sample_ts: datetime) -> list[str]:
    """Build one intrabar CSV row from a forming-bar snapshot, in header order."""
    return [
        sample_ts.isoformat(),
        bar.instrument,
        bar.ts.isoformat(),
        str(bar.open),
        str(bar.high),
        str(bar.low),
        str(bar.close),
        str(bar.volume),
    ]


def _append_intrabar_csv(
    bar: Bar, sample_ts: datetime, path: Path | None = None
) -> None:
    """Append one snapshot row to intrabar_<instrument>.csv, writing the header
    once if the file does not yet exist (single rolling file, append-forever)."""
    p = path or Path(f"intrabar_{bar.instrument}.csv")
    new_file = not p.exists()
    with p.open("a", newline="") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(_INTRABAR_HEADERS)
        writer.writerow(_intrabar_row(bar, sample_ts))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_intrabar_recorder.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/broker/topstepx.py tests/test_intrabar_recorder.py
git commit -m "feat: intrabar CSV row + append helpers (pure, tested)"
```

---

## Task 2: Sampler task lifecycle (init, loop, start, cancel)

**Files:**
- Modify: `app/broker/topstepx.py`

- [ ] **Step 1: Initialize the task handle in `__init__`**

In `app/broker/topstepx.py`, find the end of `__init__` where the forming-bar fields are initialized:

```python
        self._forming_bar: Bar | None = None
        self._forming_bar_minute: datetime | None = None
```

Add directly below them:

```python
        # Background task that snapshots the forming bar to intrabar_<instr>.csv.
        # Started at the end of subscribe(), cancelled in disconnect().
        self._intrabar_task: asyncio.Task | None = None
```

- [ ] **Step 2: Add the sampler loop method**

In `app/broker/topstepx.py`, add this method to the `TopstepXBroker` class. Place it immediately after the `subscribe(...)` method definition (after the `subscribe` method's body ends, before the next method):

```python
    async def _intrabar_sampler_loop(self) -> None:
        """Every _INTRABAR_SAMPLE_SECONDS, snapshot the forming bar to CSV.

        Pure observer: reads self._forming_bar only. A disk/serialization error
        is logged at ERROR and the loop continues — it never crashes the broker
        and never dies silently (Rule 12). CancelledError exits cleanly.
        """
        while True:
            try:
                await asyncio.sleep(_INTRABAR_SAMPLE_SECONDS)
                bar = self._forming_bar
                if bar is not None:
                    _append_intrabar_csv(bar, _utcnow())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("intrabar sampler: failed to record snapshot")
```

- [ ] **Step 3: Start the task at the end of `subscribe()`**

In `subscribe()`, the QUOTE_UPDATE handler is wired with this line:

```python
        await self._suite.events.on(EventType.QUOTE_UPDATE, _on_quote_update)
```

Immediately after that line, add:

```python
        # Start the intrabar recorder now that _forming_bar can populate.
        self._intrabar_task = asyncio.create_task(self._intrabar_sampler_loop())
        log.info(
            "Intrabar recorder started: %ds interval -> intrabar_%s.csv",
            _INTRABAR_SAMPLE_SECONDS, primary,
        )
```

- [ ] **Step 4: Cancel the task in `disconnect()`**

The current `disconnect()` reads:

```python
    async def disconnect(self) -> None:
        if self._suite is not None:
            try:
                await self._suite.disconnect()
            except Exception as e:
                log.warning("Error during disconnect: %s", e)
            self._suite = None
```

Replace it with:

```python
    async def disconnect(self) -> None:
        if self._intrabar_task is not None:
            self._intrabar_task.cancel()
            try:
                await self._intrabar_task
            except asyncio.CancelledError:
                pass
            self._intrabar_task = None
        if self._suite is not None:
            try:
                await self._suite.disconnect()
            except Exception as e:
                log.warning("Error during disconnect: %s", e)
            self._suite = None
```

- [ ] **Step 5: Verify import + syntax sanity**

Run: `.venv\Scripts\python.exe -c "import app.broker.topstepx"`
Expected: no output, exit code 0 (module imports cleanly; SDK import is lazy so this works without project-x-py loaded).

- [ ] **Step 6: Run the recorder tests + the broker test module**

Run: `.venv\Scripts\python.exe -m pytest tests/test_intrabar_recorder.py -v`
Expected: all 3 tests PASS.

Run: `.venv\Scripts\python.exe -m pytest tests/ --ignore=tests/test_backtest.py -q`
Expected: no NEW failures beyond the 5 known pre-existing ones noted in prior plans.

- [ ] **Step 7: Commit**

```bash
git add app/broker/topstepx.py
git commit -m "feat: intrabar sampler task (start in subscribe, cancel in disconnect)"
```

---

## Manual Verification (after merge, requires bot restart)

The bot is currently running (PID 15824); these steps only take effect after a restart.

- [ ] Restart the bot (use the `run-bot` skill / `deploy/windows/`).
- [ ] Within ~30 seconds of live quotes, confirm `intrabar_MGC.csv` exists in the project root.
- [ ] `Get-Content intrabar_MGC.csv -Tail 5` — confirm rows grow ~1 every 5s, header present once, `close` tracks a plausible MGC mid (~4500s), `bar_minute` rolls at minute boundaries.
- [ ] Stop the bot; confirm no error spew about the sampler task in the shutdown log.

---

## Self-Review

**Spec coverage:**
- [x] 5-second interval → `_INTRABAR_SAMPLE_SECONDS = 5` (Task 1 Step 4)
- [x] Continuous while subscribed → task started in `subscribe()`, runs until `disconnect()` (Task 2 Steps 3–4)
- [x] MGC only → naturally; filename derives from `bar.instrument` / `primary` (single-instrument broker)
- [x] Single rolling file, header once → `_append_intrabar_csv` writes header only when file absent (Task 1 Step 4, test in Step 1)
- [x] Columns `sample_ts, instrument, bar_minute, open, high, low, close, volume` → `_INTRABAR_HEADERS` + `_intrabar_row` (Task 1)
- [x] Pure observer, off hot path → reads `self._forming_bar` only, separate task (Task 2 Step 2)
- [x] Lifecycle init/start/cancel → Task 2 Steps 1, 3, 4
- [x] Error handling logs at ERROR and continues; CancelledError clean → Task 2 Step 2
- [x] One file touched (`topstepx.py`) → matches spec File Map

**Placeholder scan:** none — every code step shows complete code and exact commands.

**Type consistency:**
- `_intrabar_row(bar, sample_ts) -> list[str]` used identically in `_append_intrabar_csv` and tests.
- `_append_intrabar_csv(bar, sample_ts, path=None)` — `path` keyword used in tests with `tmp_path`; production call omits it (derives from `bar.instrument`).
- `_utcnow()` already defined at module top (returns `datetime.now(timezone.utc)`) — reused in the loop.
- `_INTRABAR_HEADERS` referenced in helper and tests with the same ordering as `_intrabar_row` output.
- `primary` is the in-scope local in `subscribe()` (the single subscribed symbol) — valid at the Step 3 insertion point.
```
