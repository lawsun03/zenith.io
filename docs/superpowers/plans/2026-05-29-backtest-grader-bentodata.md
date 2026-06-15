# Backtest Grade Scorecard + Databento Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface SetupGrader grades (A+/A/A-/B/B-) with per-criterion pass/fail in the backtest results UI, and add a Databento data-source toggle to the Run Backtest panel.

**Architecture:** (1) `app/backtest.py` emits `grade`+`criteria` on every signal and trade dict in the output JSON; (2) a new `POST /api/databento/fetch` endpoint wraps `fetch_bars_databento.py` with a cache check; (3) `BacktestsPage.tsx` adds a data-source toggle in the run panel and a clickable grade scorecard above the trade list.

**Tech Stack:** Python 3 / FastAPI (backend), React + TypeScript + Tailwind CSS (frontend)

**Spec:** `docs/superpowers/specs/2026-05-29-backtest-grader-bentodata-design.md`

---

## File Inventory

| File | Change |
|---|---|
| `app/backtest.py` | Add `grade`+`criteria` to `on_signal` capture; post-process to copy into trade dicts |
| `app/backtest/runner.py` | Same change via `_order_grades` dict; propagate into `_reconstruct_trades` |
| `app/api/server.py` | New `POST /api/databento/fetch` endpoint |
| `frontend/src/components/BacktestsPage.tsx` | Databento toggle + grade scorecard + grade filter + criteria inline |
| `tests/test_backtest_runner.py` | New tests: grade in reconstructed trades |
| `tests/test_server_databento.py` | New tests: endpoint cache-hit and subprocess paths |

---

## Task 1: Emit grade in `app/backtest/runner.py` + tests

This is the testable code path (used by `test_backtest_runner.py` and walk-forward). Modify it first so tests can verify the grade flow.

**Files:**
- Modify: `app/backtest/runner.py:232-262` (`_reconstruct_trades`) and `:276-310` (`run_backtest` inner callbacks)
- Test: `tests/test_backtest_runner.py`

### Key facts before coding

`Signal.setup_grade` is a `SetupGrade | None` (set by the grader, already on the signal when `on_signal` fires). The fills in `runner.py` are captured BEFORE `on_signal` fires (entry fill emitted inside `place_bracket`), so grade cannot be attached in `on_fill`. Instead, store grade keyed by `outcome.broker_order_id` in `on_signal`, then look it up in `_reconstruct_trades`.

`SetupGrade` fields to use:
- `grade.grade` → `"A+"` | `"A"` | `"A-"` | `"B"` | `"B-"`
- `grade.momentum_quality != "weak"` → `mom` criterion (it's a string `"strong"|"decent"|"weak"`)
- `grade.target_clear` → `tgt`
- `grade.fvg_singular` → `fvg`
- `grade.premium_discount_ok` → `pd`
- `grade.has_delivery_fvg` → `del`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_backtest_runner.py`:

```python
def test_reconstruct_trades_includes_grade():
    """Trades carry grade and criteria from their entry fill dict."""
    entry = {
        "ts": "2026-01-02T10:01:00+00:00",
        "instrument": "MGC",
        "side": "long",
        "fill_price": "100.0",
        "size": 1,
        "is_entry": True,
        "realized_pnl_delta": "-0.74",
        "killzone": "NY AM",
        "order_id": "abc123",
        "grade": "A+",
        "criteria": {"mom": True, "tgt": True, "fvg": True, "pd": True, "del": True},
    }
    exit_fill = {
        "ts": "2026-01-02T10:10:00+00:00",
        "instrument": "MGC",
        "side": "long",
        "fill_price": "102.0",
        "size": 1,
        "is_entry": False,
        "realized_pnl_delta": "10.0",
        "killzone": "NY AM",
        "order_id": "abc123-T",
    }
    trades = _reconstruct_trades([entry, exit_fill])
    assert len(trades) == 1
    assert trades[0]["grade"] == "A+"
    assert trades[0]["criteria"] == {"mom": True, "tgt": True, "fvg": True, "pd": True, "del": True}


def test_reconstruct_trades_grade_missing_when_no_grade_on_fill():
    """Trades without grade on entry fill still reconstruct correctly."""
    entry = {
        "ts": "2026-01-02T10:01:00+00:00",
        "instrument": "MGC",
        "side": "long",
        "fill_price": "100.0",
        "size": 1,
        "is_entry": True,
        "realized_pnl_delta": "-0.74",
        "killzone": "NY AM",
    }
    exit_fill = {
        "ts": "2026-01-02T10:10:00+00:00",
        "instrument": "MGC",
        "side": "long",
        "fill_price": "102.0",
        "size": 1,
        "is_entry": False,
        "realized_pnl_delta": "10.0",
        "killzone": "NY AM",
    }
    trades = _reconstruct_trades([entry, exit_fill])
    assert len(trades) == 1
    assert trades[0].get("grade") is None
    assert trades[0].get("criteria") is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv\Scripts\pytest tests\test_backtest_runner.py::test_reconstruct_trades_includes_grade tests\test_backtest_runner.py::test_reconstruct_trades_grade_missing_when_no_grade_on_fill -v
```

Expected: both FAIL — `trades[0]` has no `grade` key.

- [ ] **Step 3: Modify `app/backtest/runner.py`**

**3a.** In `_reconstruct_trades` (line 232), update signature and propagate grade from entry fill:

Replace:
```python
def _reconstruct_trades(fills: list[dict]) -> list[dict]:
    """Pair entry fills with exit fills. hold_seconds uses fill timestamps (bar time)."""
    # Assumes strict alternation: entry fill followed by exit fill.
    # A trade still open at backtest end produces a warning and is not counted.
    trades: list[dict] = []
    open_entry: dict | None = None
    for f in fills:
        if f["is_entry"]:
            open_entry = f
        elif open_entry is not None:
            entry_ts = datetime.fromisoformat(open_entry["ts"])
            exit_ts = datetime.fromisoformat(f["ts"])
            hold = int((exit_ts - entry_ts).total_seconds())
            trades.append({
                "instrument": f.get("instrument", ""),
                "side": open_entry["side"],
                "size": open_entry["size"],
                "entry_ts": open_entry["ts"],
                "entry_price": open_entry["fill_price"],
                "exit_ts": f["ts"],
                "exit_price": f["fill_price"],
                "realized_pnl": f["realized_pnl_delta"],
                "hold_seconds": hold,
            })
            open_entry = None
    if open_entry is not None:
        log.warning(
            "_reconstruct_trades: unclosed entry at bar end (entry_ts=%s)",
            open_entry["ts"],
        )
    return trades
```

With:
```python
def _reconstruct_trades(fills: list[dict]) -> list[dict]:
    """Pair entry fills with exit fills. hold_seconds uses fill timestamps (bar time)."""
    # Assumes strict alternation: entry fill followed by exit fill.
    # A trade still open at backtest end produces a warning and is not counted.
    trades: list[dict] = []
    open_entry: dict | None = None
    for f in fills:
        if f["is_entry"]:
            open_entry = f
        elif open_entry is not None:
            entry_ts = datetime.fromisoformat(open_entry["ts"])
            exit_ts = datetime.fromisoformat(f["ts"])
            hold = int((exit_ts - entry_ts).total_seconds())
            trade: dict = {
                "instrument": f.get("instrument", ""),
                "side": open_entry["side"],
                "size": open_entry["size"],
                "entry_ts": open_entry["ts"],
                "entry_price": open_entry["fill_price"],
                "exit_ts": f["ts"],
                "exit_price": f["fill_price"],
                "realized_pnl": f["realized_pnl_delta"],
                "hold_seconds": hold,
            }
            if open_entry.get("grade") is not None:
                trade["grade"] = open_entry["grade"]
                trade["criteria"] = open_entry["criteria"]
            trades.append(trade)
            open_entry = None
    if open_entry is not None:
        log.warning(
            "_reconstruct_trades: unclosed entry at bar end (entry_ts=%s)",
            open_entry["ts"],
        )
    return trades
```

**3b.** Inside `run_backtest` (around line 276), add `_order_grades` dict alongside `_order_killzones`, and update `on_signal` and `on_fill` to wire grade through fills:

Replace:
```python
    fills_captured: list[dict] = []
    rejected_signals = 0
    # Maps entry order_id → killzone name so exit fills can be tagged.
    # on_signal fires after place_bracket (entry fill already emitted), so
    # entry fills get "unknown"; exit fills always get the correct killzone.
    _order_killzones: dict[str, str] = {}

    def _kz_for_fill(broker_order_id: str | None) -> str:
        if not broker_order_id:
            return "unknown"
        # Exit fills end with -X / -P / -S / -T; strip to recover entry order_id.
        bid = broker_order_id
        if len(bid) > 2 and bid[-2] == "-" and bid[-1] in "XPST":
            bid = bid[:-2]
        return _order_killzones.get(bid, "unknown")

    async def on_signal(signal: Signal, outcome: OrderOutcome) -> None:
        nonlocal rejected_signals
        if not outcome.placed:
            rejected_signals += 1
            return
        if outcome.broker_order_id:
            _order_killzones[outcome.broker_order_id] = signal.killzone

    async def on_fill(fill: Fill) -> None:
        fills_captured.append({
            "ts": fill.ts.isoformat(),
            "instrument": fill.instrument,
            "side": fill.side,
            "fill_price": str(fill.fill_price),
            "size": fill.size,
            "is_entry": fill.is_entry,
            "realized_pnl_delta": str(fill.realized_pnl_delta),
            "killzone": _kz_for_fill(fill.broker_order_id),
        })
```

With:
```python
    fills_captured: list[dict] = []
    rejected_signals = 0
    # Maps entry order_id → killzone name so exit fills can be tagged.
    # on_signal fires after place_bracket (entry fill already emitted), so
    # entry fills get "unknown"; exit fills always get the correct killzone.
    _order_killzones: dict[str, str] = {}
    # Maps entry order_id → {"grade": str, "criteria": dict} for grade propagation.
    # Same timing constraint as killzones: populated in on_signal, applied
    # retroactively to the already-captured entry fill dict via order_id lookup
    # in _reconstruct_trades (not in on_fill, which fires before on_signal).
    _order_grades: dict[str, dict] = {}

    def _kz_for_fill(broker_order_id: str | None) -> str:
        if not broker_order_id:
            return "unknown"
        # Exit fills end with -X / -P / -S / -T; strip to recover entry order_id.
        bid = broker_order_id
        if len(bid) > 2 and bid[-2] == "-" and bid[-1] in "XPST":
            bid = bid[:-2]
        return _order_killzones.get(bid, "unknown")

    async def on_signal(signal: Signal, outcome: OrderOutcome) -> None:
        nonlocal rejected_signals
        if not outcome.placed:
            rejected_signals += 1
            return
        if outcome.broker_order_id:
            _order_killzones[outcome.broker_order_id] = signal.killzone
            g = signal.setup_grade
            if g is not None:
                _order_grades[outcome.broker_order_id] = {
                    "grade": g.grade,
                    "criteria": {
                        "mom": g.momentum_quality != "weak",
                        "tgt": g.target_clear,
                        "fvg": g.fvg_singular,
                        "pd": g.premium_discount_ok,
                        "del": g.has_delivery_fvg,
                    },
                }

    async def on_fill(fill: Fill) -> None:
        fill_dict: dict = {
            "ts": fill.ts.isoformat(),
            "instrument": fill.instrument,
            "side": fill.side,
            "fill_price": str(fill.fill_price),
            "size": fill.size,
            "is_entry": fill.is_entry,
            "realized_pnl_delta": str(fill.realized_pnl_delta),
            "killzone": _kz_for_fill(fill.broker_order_id),
            "order_id": fill.broker_order_id or "",
        }
        fills_captured.append(fill_dict)
```

**3c.** Update the call to `_reconstruct_trades` (near the end of `run_backtest`) to retroactively apply grade to the entry fill dict before pairing:

Find the line:
```python
    trades = _reconstruct_trades(fills_captured)
```

Replace with:
```python
    # Retroactively attach grade to entry fill dicts now that on_signal has fired.
    for fill_dict in fills_captured:
        if fill_dict["is_entry"]:
            grade_info = _order_grades.get(fill_dict.get("order_id", ""))
            if grade_info:
                fill_dict["grade"] = grade_info["grade"]
                fill_dict["criteria"] = grade_info["criteria"]
    trades = _reconstruct_trades(fills_captured)
```

- [ ] **Step 4: Run tests to verify they pass**

```
.venv\Scripts\pytest tests\test_backtest_runner.py::test_reconstruct_trades_includes_grade tests\test_backtest_runner.py::test_reconstruct_trades_grade_missing_when_no_grade_on_fill -v
```

Expected: both PASS.

- [ ] **Step 5: Run full backtest test suite to check no regressions**

```
.venv\Scripts\pytest tests\test_backtest_runner.py tests\test_backtest.py -v
```

Expected: all existing tests PASS.

- [ ] **Step 6: Commit**

```
git add app/backtest/runner.py tests/test_backtest_runner.py
git commit -m "feat: propagate SetupGrade to trade dicts in backtest runner"
```

---

## Task 2: Emit grade in `app/backtest.py` (server subprocess path)

The API server runs `python -m app.backtest` as a subprocess. This file writes the JSON that the UI reads. It uses the same `Signal.setup_grade` field but has a different callback structure (signals ARE captured, and grade can be post-processed into trades by matching on side+entry_price).

**Files:**
- Modify: `app/backtest.py:178-192` (`on_signal`) and `app/backtest.py:226-242` (post-processing after `reconstruct_trades`)

- [ ] **Step 1: Update `on_signal` to capture grade**

In `app/backtest.py`, replace the `on_signal` callback body:

```python
    async def on_signal(signal: Signal, outcome: OrderOutcome) -> None:
        signals_captured.append({
            "ts": signal.created_at.isoformat(),
            "side": signal.side,
            "entry": str(signal.entry),
            "stop": str(signal.stop),
            "target": str(signal.target),
            "killzone": signal.killzone,
            "rationale": signal.rationale,
            "outcome": {
                "placed": outcome.placed,
                "reason": outcome.reason,
                "allowed_size": outcome.allowed_size,
            },
        })
```

With:
```python
    async def on_signal(signal: Signal, outcome: OrderOutcome) -> None:
        g = signal.setup_grade
        signals_captured.append({
            "ts": signal.created_at.isoformat(),
            "side": signal.side,
            "entry": str(signal.entry),
            "stop": str(signal.stop),
            "target": str(signal.target),
            "killzone": signal.killzone,
            "rationale": signal.rationale,
            "grade": g.grade if g is not None else None,
            "criteria": {
                "mom": g.momentum_quality != "weak",
                "tgt": g.target_clear,
                "fvg": g.fvg_singular,
                "pd": g.premium_discount_ok,
                "del": g.has_delivery_fvg,
            } if g is not None else None,
            "outcome": {
                "placed": outcome.placed,
                "reason": outcome.reason,
                "allowed_size": outcome.allowed_size,
            },
        })
```

- [ ] **Step 2: Post-process trades to copy grade from matched signal**

In `app/backtest.py`, find the lines after `run_backtest` is called and `trades` is computed (around line 226):

```python
    stats = compute_stats(fills_captured)
    trades = reconstruct_trades(fills_captured)
```

Replace with:
```python
    stats = compute_stats(fills_captured)
    trades = reconstruct_trades(fills_captured)

    # Copy grade from placed signal → trade by matching on (side, entry_price).
    # In paper replay mode entry_price == signal.entry exactly (no slippage divergence
    # on the string comparison that would matter here).
    _signal_grade_by_key: dict[tuple[str, str], dict] = {}
    for s in signals_captured:
        if s.get("grade") is not None and s.get("outcome", {}).get("placed"):
            _signal_grade_by_key[(s["side"], s["entry"])] = {
                "grade": s["grade"],
                "criteria": s["criteria"],
            }
    for t in trades:
        grade_info = _signal_grade_by_key.get((t["side"], t["entry_price"]))
        if grade_info is not None:
            t["grade"] = grade_info["grade"]
            t["criteria"] = grade_info["criteria"]
```

- [ ] **Step 3: Verify manually**

Run a backtest from the UI (or via curl: `POST /api/backtest/run`) and inspect the resulting JSON in `backtests/`:

```powershell
# After a backtest completes, check the newest JSON:
Get-ChildItem backtests\*.json | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | Get-Content | python -c "import sys,json; d=json.load(sys.stdin); print([t.get('grade') for t in d.get('trades',[])])"
```

Expected: a list like `['A+', 'A-', 'A', None]` (None for trades where grade wasn't set).

- [ ] **Step 4: Commit**

```
git add app/backtest.py
git commit -m "feat: add grade+criteria to backtest.py signal and trade output"
```

---

## Task 3: `POST /api/databento/fetch` endpoint

**Files:**
- Modify: `app/api/server.py`
- Test: `tests/test_server_databento.py` (new file)

The endpoint wraps `scripts/fetch_bars_databento.py`. It checks the existing CSV max date before fetching. Supports `dry_run=true` for cost-estimate-only.

### How the script works

`python scripts/fetch_bars_databento.py --symbol GC.c.0 --start 2026-01-01 --end 2026-05-29 --estimate-only`

Prints one line: `[estimate] ohlcv-1m GC.c.0 2026-01-01T00:00:00 -> 2026-05-29T00:00:00: $0.1234`
Then exits. Cost is parsed with `re.search(r'\$(\d+\.\d+)', line)`.

Without `--estimate-only`, fetches and writes to `--out ./bars_MGC.csv`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_server_databento.py`:

```python
"""Tests for POST /api/databento/fetch endpoint."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient


def _make_app():
    """Build a minimal app instance for testing."""
    import os
    os.environ.setdefault("TOPSTEP_BOT_MODE", "paper")
    from app.api.server import build_app
    return build_app(mode="paper", bot_config_path="bot_config.json")


@pytest.fixture
def client():
    app = _make_app()
    return TestClient(app)


def test_databento_fetch_missing_api_key(client, monkeypatch):
    """Returns ok=false immediately when DATABENTO_API_KEY is absent."""
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    resp = client.post(
        "/api/databento/fetch",
        json={"start": "2026-01-01", "end": "2026-05-29", "symbol": "MGC", "dry_run": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "DATABENTO_API_KEY" in body["reason"]


def test_databento_fetch_dry_run_returns_estimate(client, monkeypatch, tmp_path):
    """dry_run=true calls script with --estimate-only and returns cost without fetching."""
    monkeypatch.setenv("DATABENTO_API_KEY", "fake-key")
    # Patch subprocess.run to return the estimate output format
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "[estimate] ohlcv-1m GC.c.0 2026-01-01T00:00:00 -> 2026-05-29T00:00:00: $0.4200\n"
    fake_result.stderr = ""

    with patch("app.api.server.subprocess.run", return_value=fake_result) as mock_run:
        resp = client.post(
            "/api/databento/fetch",
            json={"start": "2026-01-01", "end": "2026-05-29", "symbol": "MGC", "dry_run": True},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert abs(body["cost_estimate"] - 0.42) < 0.001
    assert body["days_fetched"] == 0
    # Must have called with --estimate-only
    args_used = mock_run.call_args[0][0]
    assert "--estimate-only" in args_used


def test_databento_fetch_cache_hit_skips_download(client, monkeypatch, tmp_path):
    """If CSV already covers the requested end date, skip the download."""
    monkeypatch.setenv("DATABENTO_API_KEY", "fake-key")
    # Write a CSV covering through 2026-05-29
    csv_path = tmp_path / "bars_MGC.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-05-29T23:59:00+00:00,100,101,99,100,10\n"
    )
    # Patch the CSV path lookup
    with patch("app.api.server._bars_csv_path", return_value=str(csv_path)):
        resp = client.post(
            "/api/databento/fetch",
            json={"start": "2026-01-01", "end": "2026-05-29", "symbol": "MGC", "dry_run": False},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["days_fetched"] == 0
    assert body["cached_through"] == "2026-05-29"
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv\Scripts\pytest tests\test_server_databento.py -v
```

Expected: FAIL — `POST /api/databento/fetch` does not exist yet.

- [ ] **Step 3: Add helper and endpoint to `app/api/server.py`**

Find the section in `server.py` around line 950 (before the backtest endpoints). Add after the `backtests_dir = Path("backtests")` line, inserting a new Pydantic model and endpoint.

**3a.** Add the request model (find where other `class ...Request(BaseModel):` models are defined, around line 950+):

```python
class DatabentofetchRequest(BaseModel):
    start: str       # "YYYY-MM-DD"
    end: str         # "YYYY-MM-DD"
    symbol: str      # "MGC" — mapped to GC.c.0 for Databento
    dry_run: bool = False
```

**3b.** Add the helper to resolve the bars CSV path (add near other Path-based helpers, above the backtest endpoints):

```python
def _bars_csv_path(symbol: str) -> str:
    """Return the local bars CSV path for the given instrument symbol."""
    return f"bars_{symbol.upper()}.csv"


def _csv_cached_through(csv_path: str) -> str | None:
    """Return the YYYY-MM-DD of the last bar in the CSV, or None if absent/empty."""
    p = Path(csv_path)
    if not p.exists():
        return None
    last_ts = ""
    try:
        with p.open(newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = row.get("timestamp", "")
                if ts:
                    last_ts = ts
    except Exception:
        return None
    return last_ts[:10] if last_ts else None  # "YYYY-MM-DD"
```

**3c.** Add the endpoint (insert before `@app.post("/api/backtest/run")`):

```python
    @app.post("/api/databento/fetch")
    async def databento_fetch(req: DatabentofetchRequest) -> JSONResponse:
        """
        Fetch Databento bars for the requested date range and symbol.
        Skips the download if the local CSV already covers req.end.
        With dry_run=True, returns cost estimate without downloading.
        """
        api_key = os.environ.get("DATABENTO_API_KEY")
        if not api_key:
            return JSONResponse({"ok": False, "reason": "DATABENTO_API_KEY not set in .env"})

        # Databento uses GC.c.0 (front-month gold) for MGC price data.
        db_symbol = "GC.c.0"
        csv_path = _bars_csv_path(req.symbol)
        cached_through = _csv_cached_through(csv_path)

        # Cache hit: CSV already covers the requested end date.
        if not req.dry_run and cached_through is not None and cached_through >= req.end:
            return JSONResponse({
                "ok": True,
                "days_fetched": 0,
                "cached_through": cached_through,
                "cost_estimate": 0.0,
            })

        # Build script args
        script = Path(__file__).resolve().parent.parent.parent / "scripts" / "fetch_bars_databento.py"
        cmd = [
            sys.executable, str(script),
            "--symbol", db_symbol,
            "--start", req.start,
            "--end", req.end,
            "--out", csv_path,
        ]
        if req.dry_run:
            cmd.append("--estimate-only")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return JSONResponse({"ok": False, "reason": "Databento fetch timed out (>120s)"}, status_code=500)
        except Exception as e:
            return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)

        if result.returncode != 0:
            return JSONResponse({
                "ok": False,
                "reason": result.stderr.strip() or f"Script exited with code {result.returncode}",
            }, status_code=500)

        # Parse cost from stdout line: "[estimate] ohlcv-1m GC.c.0 ... $0.1234"
        import re as _re
        cost_match = _re.search(r'\$(\d+\.\d+)', result.stdout)
        cost = float(cost_match.group(1)) if cost_match else 0.0

        new_cached_through = _csv_cached_through(csv_path) if not req.dry_run else cached_through

        return JSONResponse({
            "ok": True,
            "days_fetched": 0 if req.dry_run else 1,  # non-zero signals fetch happened
            "cached_through": new_cached_through or req.end,
            "cost_estimate": cost,
        })
```

- [ ] **Step 4: Run tests to verify they pass**

```
.venv\Scripts\pytest tests\test_server_databento.py -v
```

Expected: all PASS.

- [ ] **Step 5: Run existing API tests to check no regressions**

```
.venv\Scripts\pytest tests\test_api.py -v
```

Expected: all existing tests PASS.

- [ ] **Step 6: Commit**

```
git add app/api/server.py tests/test_server_databento.py
git commit -m "feat: add POST /api/databento/fetch endpoint with cache check"
```

---

## Task 4: Frontend — Databento data source toggle

**Files:**
- Modify: `frontend/src/components/BacktestsPage.tsx`

Add `dataSource` state, the toggle UI row, and update `startRun` to call the fetch endpoint first when Databento is selected.

- [ ] **Step 1: Add new state and types**

In `BacktestsPage.tsx`, after the existing state declarations (around line 207), add:

```typescript
  type DataSource = 'local' | 'databento'
  const [dataSource, setDataSource] = useState<DataSource>('local')
  const [bentoMeta, setBentoMeta] = useState<{
    cost: number
    cachedThrough: string | null
    willFetch: number
  } | null>(null)
  const [bentoLoading, setBentoLoading] = useState(false)
```

- [ ] **Step 2: Add handler to probe Databento estimate when toggle switches**

Add this function in the component body (after `resetStrategyToConfig`):

```typescript
  async function switchToDataSource(src: DataSource) {
    setDataSource(src)
    setBentoMeta(null)
    if (src !== 'databento') return
    setBentoLoading(true)
    try {
      const cfg = await fetch('/api/config').then(r => r.json())
      const symbol = cfg?.instrument ?? 'MGC'
      const res = await fetch('/api/databento/fetch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          start: startDate,
          end: endDate,
          symbol,
          dry_run: true,
        }),
      })
      const body = await res.json()
      if (body.ok) {
        setBentoMeta({
          cost: body.cost_estimate ?? 0,
          cachedThrough: body.cached_through ?? null,
          willFetch: body.days_fetched ?? 0,
        })
      } else {
        setMsg(`Databento: ${body.reason}`)
        setDataSource('local')
      }
    } catch {
      setMsg('Databento probe failed')
      setDataSource('local')
    } finally {
      setBentoLoading(false)
    }
  }
```

- [ ] **Step 3: Update `startRun` to call `/api/databento/fetch` first when source is Databento**

In the existing `startRun` function, before the `fetch('/api/backtest/run', ...)` call, insert:

```typescript
    // If Databento source, fetch bars first.
    if (dataSource === 'databento') {
      setMsg('Fetching bars from Databento…')
      const cfg = await fetch('/api/config').then(r => r.json())
      const symbol = cfg?.instrument ?? 'MGC'
      const bentoRes = await fetch('/api/databento/fetch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          start: startDate,
          end: endDate,
          symbol,
          dry_run: false,
        }),
      })
      const bentoBody = await bentoRes.json()
      if (!bentoBody.ok) {
        setMsg(`Databento fetch failed: ${bentoBody.reason}`)
        setRunning(false)
        return
      }
    }
```

Insert this block at the start of the `try` block in `startRun`, BEFORE the existing `const beforeCount = list.length` line.

- [ ] **Step 4: Add the data source toggle UI**

In the JSX, find the closing `</details>` tag for the strategy params section (around line 711) and insert the toggle immediately after it, before the label+button row:

```tsx
          {/* Data source toggle */}
          <div className="border border-border bg-bg/30 p-3">
            <div className="text-[9px] tracking-widest text-dim uppercase mb-2">Data Source</div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => switchToDataSource('local')}
                disabled={running}
                className={`text-[11px] tracking-widest uppercase px-3 py-1 border transition-colors ${
                  dataSource === 'local'
                    ? 'border-accent text-accent bg-accent/10'
                    : 'border-border text-dim hover:text-ink'
                }`}
              >
                Local CSV
              </button>
              <button
                onClick={() => switchToDataSource('databento')}
                disabled={running}
                className={`text-[11px] tracking-widest uppercase px-3 py-1 border transition-colors ${
                  dataSource === 'databento'
                    ? 'border-warn text-warn bg-warn/10'
                    : 'border-border text-dim hover:text-ink'
                }`}
              >
                Databento
              </button>
              {bentoLoading && (
                <span className="text-[10px] text-dim ml-2">probing…</span>
              )}
              {dataSource === 'databento' && bentoMeta && !bentoLoading && (
                <span className="text-[10px] text-dim ml-2 font-mono">
                  est. <span className="text-warn">${bentoMeta.cost.toFixed(2)}</span>
                  {bentoMeta.cachedThrough && (
                    <> · cached through <span className="text-ink">{bentoMeta.cachedThrough}</span></>
                  )}
                  {bentoMeta.willFetch === 0 && (
                    <> · <span className="text-accent">full cache hit</span></>
                  )}
                </span>
              )}
            </div>
          </div>
```

- [ ] **Step 5: Build and verify**

```
cd frontend && npm run build
```

Expected: build succeeds with no TypeScript errors.

Open `http://localhost:5173` → Backtests tab → verify the Local CSV / Databento toggle appears between the strategy params and the Run button. Switch to Databento — confirm the probe runs (brief "probing…" text) and cost/cache info appears.

- [ ] **Step 6: Commit**

```
git add frontend/src/components/BacktestsPage.tsx
git commit -m "feat: add Databento data source toggle to backtest run panel"
```

---

## Task 5: Frontend — grade scorecard + filter + criteria inline

**Files:**
- Modify: `frontend/src/components/BacktestsPage.tsx`

This task adds the grade scorecard tiles above the trade list, grade filtering, and the criteria row on each trade. The existing `<table>` trade list is converted to a `<div>` list to support the per-trade sub-row for criteria.

- [ ] **Step 1: Add new types**

In `BacktestsPage.tsx`, update the `Trade` interface (around line 30) and add grade-related types:

```typescript
interface GradeCriteria {
  mom: boolean
  tgt: boolean
  fvg: boolean
  pd: boolean
  del: boolean
}

interface Trade {
  entry_ts: string
  exit_ts: string
  side: string
  entry_price: string
  exit_price: string
  size: number
  pnl: string
  grade?: string
  criteria?: GradeCriteria
}

const GRADE_TIERS = ['A+', 'A', 'A-', 'B', 'B-'] as const
type GradeTier = typeof GRADE_TIERS[number]

const CRITERIA_KEYS: Array<keyof GradeCriteria> = ['mom', 'tgt', 'fvg', 'pd', 'del']
```

- [ ] **Step 2: Add grade filter state and helpers**

After the existing state declarations, add:

```typescript
  const [gradeFilter, setGradeFilter] = useState<string | null>(null)
```

Add the following helper functions in the component body (after `switchToDataSource`):

```typescript
  function gradeColor(g: string): string {
    switch (g) {
      case 'A+': return 'border-accent text-accent bg-accent/10'
      case 'A':  return 'border-accent/60 text-accent/80 bg-accent/5'
      case 'A-': return 'border-warn/60 text-warn bg-warn/5'
      case 'B':  return 'border-warn/30 text-warn/60 bg-warn/5'
      case 'B-': return 'border-danger/40 text-danger/70 bg-danger/5'
      default:   return 'border-border text-dim'
    }
  }

  function gradeBadge(g: string | undefined): string {
    switch (g) {
      case 'A+': return 'bg-accent text-bg'
      case 'A':  return 'bg-accent/70 text-bg'
      case 'A-': return 'bg-warn text-bg'
      case 'B':  return 'bg-warn/60 text-bg'
      case 'B-': return 'bg-danger/70 text-bg'
      default:   return 'bg-dim/20 text-dim'
    }
  }
```

- [ ] **Step 3: Add grade scorecard computation**

Add a `useMemo` after the state declarations. Import `useMemo` if not already imported (it typically is in React):

```typescript
  const gradeStats = useMemo(() => {
    if (!selected?.trades) return {} as Record<GradeTier, { count: number; winRate: number; profitFactor: number | null }>
    return GRADE_TIERS.reduce((acc, g) => {
      const gt = selected.trades.filter(t => t.grade === g)
      const wins = gt.filter(t => parseFloat(t.pnl) > 0)
      const losses = gt.filter(t => parseFloat(t.pnl) < 0)
      const grossWin = wins.reduce((s, t) => s + parseFloat(t.pnl), 0)
      const grossLoss = Math.abs(losses.reduce((s, t) => s + parseFloat(t.pnl), 0))
      acc[g] = {
        count: gt.length,
        winRate: gt.length > 0 ? Math.round(wins.length / gt.length * 100) : 0,
        profitFactor: grossLoss > 0 ? Math.round((grossWin / grossLoss) * 100) / 100 : null,
      }
      return acc
    }, {} as Record<GradeTier, { count: number; winRate: number; profitFactor: number | null }>)
  }, [selected])

  const displayedTrades = useMemo(() => {
    if (!selected?.trades) return []
    if (!gradeFilter) return selected.trades
    return selected.trades.filter(t => t.grade === gradeFilter)
  }, [selected, gradeFilter])
```

Also reset the grade filter when the selected backtest changes. Find the `setSelected` call and reset alongside it, or add a `useEffect`:

```typescript
  useEffect(() => {
    setGradeFilter(null)
  }, [selected?.id])
```

- [ ] **Step 4: Replace the trade table with grade scorecard + div-based trade list**

Find the Trades section in the JSX (around line 1027-1062):

```tsx
                <div>
                  <div className="text-[10px] tracking-[0.3em] text-dim uppercase mb-2">Trades</div>
                  <div className="max-h-[40vh] overflow-y-auto feed border border-border">
                    <table className="w-full text-[11px] font-mono tabular-nums">
                      <thead className="text-dim text-[10px] tracking-widest uppercase">
                        <tr>
                          <th className="text-left px-3 py-2">Entry</th>
                          <th className="text-left px-3 py-2">Exit</th>
                          <th className="text-left px-3 py-2">Side</th>
                          <th className="text-right px-3 py-2">In</th>
                          <th className="text-right px-3 py-2">Out</th>
                          <th className="text-right px-3 py-2">P&L</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selected.trades.map((t, i) => {
                          const pnl = parseFloat(t.pnl)
                          return (
                            <tr key={i} className="border-t border-border">
                              <td className="px-3 py-1 text-dim">{fmtBarTs(t.entry_ts)}</td>
                              <td className="px-3 py-1 text-dim">{fmtBarTs(t.exit_ts)}</td>
                              <td className={`px-3 py-1 ${t.side === 'long' ? 'text-accent' : 'text-danger'}`}>
                                {t.side.toUpperCase()}
                              </td>
                              <td className="px-3 py-1 text-right">{t.entry_price}</td>
                              <td className="px-3 py-1 text-right">{t.exit_price}</td>
                              <td className={`px-3 py-1 text-right ${pnl >= 0 ? 'text-accent' : 'text-danger'}`}>
                                {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
```

Replace with:

```tsx
                {/* Grade scorecard tiles */}
                {selected.trades.some(t => t.grade) && (
                  <div>
                    <div className="text-[10px] tracking-[0.3em] text-dim uppercase mb-2">
                      Grade Breakdown <span className="text-dim/50 normal-case tracking-normal">· click to filter</span>
                    </div>
                    <div className="grid grid-cols-5 gap-px bg-border border border-border mb-3">
                      {GRADE_TIERS.map(g => {
                        const s = gradeStats[g] ?? { count: 0, winRate: 0, profitFactor: null }
                        const isActive = gradeFilter === g
                        return (
                          <button
                            key={g}
                            onClick={() => setGradeFilter(isActive ? null : g)}
                            className={`p-2 text-center bg-panel transition-colors ${
                              isActive ? gradeColor(g) : 'text-dim hover:text-ink'
                            } ${s.count === 0 ? 'opacity-30 cursor-default' : 'cursor-pointer'}`}
                            disabled={s.count === 0}
                          >
                            <div className={`text-base font-bold font-mono ${isActive ? '' : 'text-inherit'}`}>{g}</div>
                            <div className="text-[9px] text-dim mt-0.5">{s.count} trade{s.count !== 1 ? 's' : ''}</div>
                            {s.count > 0 && (
                              <>
                                <div className="text-[10px] font-mono">{s.winRate}% WR</div>
                                <div className="text-[10px] font-mono">
                                  {s.profitFactor !== null ? `PF ${s.profitFactor.toFixed(1)}` : 'PF —'}
                                </div>
                              </>
                            )}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                )}

                {/* Trade list */}
                <div>
                  <div className="text-[10px] tracking-[0.3em] text-dim uppercase mb-2">
                    Trades
                    {gradeFilter && (
                      <span className="text-warn normal-case tracking-normal ml-2">
                        · {gradeFilter} only ({displayedTrades.length} of {selected.trades.length})
                      </span>
                    )}
                    {!gradeFilter && ` (${selected.trades.length})`}
                  </div>
                  <div className="max-h-[40vh] overflow-y-auto feed border border-border divide-y divide-border">
                    {displayedTrades.map((t, i) => {
                      const pnl = parseFloat(t.pnl)
                      return (
                        <div key={i} className="px-3 py-2 text-[11px] font-mono">
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-3">
                              <span className="text-dim">{fmtBarTs(t.entry_ts)}</span>
                              <span className={t.side === 'long' ? 'text-accent' : 'text-danger'}>
                                {t.side.toUpperCase()}
                              </span>
                              <span className="text-dim tabular-nums">
                                {t.entry_price} → {t.exit_price}
                              </span>
                            </div>
                            <div className="flex items-center gap-2">
                              <span className={`tabular-nums font-bold ${pnl >= 0 ? 'text-accent' : 'text-danger'}`}>
                                {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                              </span>
                              {t.grade && (
                                <span className={`text-[9px] px-1.5 py-0.5 rounded-sm font-bold ${gradeBadge(t.grade)}`}>
                                  {t.grade}
                                </span>
                              )}
                            </div>
                          </div>
                          {t.criteria && (
                            <div className="flex gap-3 mt-1 text-[9px]">
                              {CRITERIA_KEYS.map(k => (
                                <span key={k} className={t.criteria![k] ? 'text-accent' : 'text-danger'}>
                                  {k}{t.criteria![k] ? '✓' : '✗'}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      )
                    })}
                    {displayedTrades.length === 0 && (
                      <div className="px-3 py-4 text-[11px] text-dim">No trades match the current filter.</div>
                    )}
                  </div>
                </div>
```

- [ ] **Step 5: Build and verify TypeScript is clean**

```
cd frontend && npm run build
```

Expected: 0 TypeScript errors, build succeeds.

- [ ] **Step 6: Verify in browser**

Open `http://localhost:5173` → Backtests tab → select an existing backtest run. Confirm:
- Grade scorecard tiles render above the trade list (or are hidden if no trades have grades yet — run a new backtest to get graded trades)
- Clicking a grade tile filters the trade list; clicking again shows all
- Each trade row shows grade badge and criteria row when `grade`/`criteria` are present
- No console errors

- [ ] **Step 7: Commit**

```
git add frontend/src/components/BacktestsPage.tsx
git commit -m "feat: grade scorecard + filter + criteria inline in backtest trade list"
```

---

## Self-Review Checklist

### Spec coverage

| Spec requirement | Covered by |
|---|---|
| `grade`+`criteria` on every signal/trade in backtest JSON | Tasks 1 + 2 |
| Grade scorecard tiles with count, WR, PF per tier | Task 5 Step 3-4 |
| Clickable tiles filter the trade list; click again clears | Task 5 Step 2+4 |
| Grade badge on trade rows | Task 5 Step 4 |
| Criteria checklist inline (`mom✓ tgt✗ ...`) | Task 5 Step 4 |
| Data source toggle (Local CSV / Databento) in run panel | Task 4 |
| Databento probe shows cost+cache on toggle | Task 4 Step 2 |
| `POST /api/databento/fetch` with dry_run + cache check | Task 3 |
| `DATABENTO_API_KEY` guard | Task 3 Step 3c |
| `npm run build` clean | Task 4 Step 5, Task 5 Step 5 |

### Placeholder scan

No TBD / TODO in any code block above. All method signatures, field names, and Tailwind classes are concrete.

### Type consistency

- `GradeCriteria` defined in Task 5 Step 1 and used in Task 5 Steps 2–4: consistent.
- `GradeTier` and `GRADE_TIERS` defined once and used in scorecard and filter: consistent.
- `gradeStats[g]` typed as `Record<GradeTier, { count, winRate, profitFactor }>`: consistent with usage.
- `DatabentofetchRequest.symbol` used as `req.symbol` throughout Task 3: consistent.
- `_order_grades` dict in `runner.py` is `dict[str, dict]` populated in `on_signal`, consumed retroactively before `_reconstruct_trades`: consistent with fill timing constraint.
