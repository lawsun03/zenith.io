# UI Backtest Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the UI-spawned backtest subprocess (`python -m app.backtest`) delegate to the canonical `app/backtest/runner.py:run_backtest`, eliminating the second, less-faithful backtest code path while preserving the exact result-JSON shape the frontend already reads.

**Architecture:** `app/backtest.py` currently has its *own* single-instrument `run_backtest` returning a hand-built dict. The canonical `app/backtest/runner.py:run_backtest(cfg: BacktestConfig)` is the faithful path (supports `combined` engine + VolumeProfile, `enforce_risk_limits`, grade/displacement/FVG capture) and returns a `BacktestResult` dataclass. We add a serializer that converts `BacktestResult` → the legacy result dict, capture `fills` in the canonical runner (it currently omits them), then rewire `app/backtest.py`'s CLI `main()` to build a `BacktestConfig` and call the canonical runner. A golden parity test locks the JSON contract before the rewire.

**Tech Stack:** Python 3.12, asyncio, pytest, dataclasses, `app/backtest/runner.py`, `app/backtest.py`, `app/api/server.py` (subprocess spawn at `/api/backtest/run`), React frontend `frontend/src/` (consumes `<run_id>.json`).

**Run all Python via the project venv:** `.venv/Scripts/python.exe`.

---

## Background: the exact contract to preserve

The frontend reads `backtests/<run_id>.json`. The legacy `app/backtest.py` writes a dict with these top-level keys (verified at `app/backtest.py:66-78`, augmented in `main()` at `:300-303`):

```
id, label, started_at, completed_at,
stats   -> dict (must contain at least: trades, win_rate, net_pnl)
trades  -> list[dict]
signals -> list[dict]
fills   -> list[dict]
config  -> dict (the BotConfig/params used)
bars_processed, rejected_signals
```

`server.py` log line at `:307-311` reads `result["stats"]["trades"]`, `["win_rate"]`, `["net_pnl"]` — these three keys are load-bearing and MUST survive.

`BacktestResult` (`app/backtest/runner.py:125-132`) has: `config, stats (BacktestStats), trades, bars_processed, rejected_signals, label, signals`. It does **NOT** carry `fills`. `BacktestStats` (`:69-89`) already has `trades`, `win_rate`, `net_pnl` plus many more fields (Decimals, an `equity_curve: list[tuple[datetime, Decimal]]`, nested dicts) — so serialization must JSON-coerce Decimal/datetime.

---

## File Structure

- **Modify** `app/backtest/runner.py` — add `fills: list[dict]` to `BacktestResult`; populate it; add `result_to_dict(result, *, id, label, started_at, completed_at) -> dict` serializer.
- **Modify** `app/backtest.py` — delete the local `run_backtest` + inline bar loop + local `_build_runner`; rewrite `main()` to build a `BacktestConfig` from CLI args and call the canonical runner + serializer. Keep the exact same argparse surface and output path.
- **Create** `tests/test_backtest_ui_parity.py` — golden parity test (legacy dict shape vs new serializer) + CLI smoke test.
- **Create** `tests/fixtures/bars_parity_mini.csv` — tiny deterministic bars slice for fast tests.

**Out of scope (follow-up plan — do NOT build here):** true multi-symbol backtests in the UI (MGC+MNQ+MES in one run). `BacktestConfig.instrument` is a single `str`; multi-symbol needs a separate design (multiple bars files, per-instrument runners, merged equity curve). This plan unifies on the canonical *single-instrument* runner first, which is the prerequisite. File a follow-up: `docs/superpowers/plans/<date>-multi-symbol-backtest.md`.

---

## Task 1: Create a deterministic mini bars fixture

**Files:**
- Create: `tests/fixtures/bars_parity_mini.csv`

- [ ] **Step 1: Generate the fixture from a real bars file (first 400 data rows)**

Run:
```bash
mkdir -p tests/fixtures
head -n 401 bars/bars_MNQ_dbv_2021_2026.csv > tests/fixtures/bars_parity_mini.csv
wc -l tests/fixtures/bars_parity_mini.csv
```
Expected: `401 tests/fixtures/bars_parity_mini.csv` (1 header + 400 rows).

- [ ] **Step 2: Confirm it loads**

Run:
```bash
.venv/Scripts/python.exe -c "from app.replay import load_bars_csv; n=sum(1 for _ in load_bars_csv('tests/fixtures/bars_parity_mini.csv', instrument='MNQ', timeframe='1min')); print('bars', n)"
```
Expected: prints `bars 400` (or close — the loader may filter session hours; any non-zero count is fine, note the number).

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/bars_parity_mini.csv
git commit -m "test: add mini bars fixture for backtest parity tests"
```

---

## Task 2: Add `fills` to `BacktestResult` and capture them

**Files:**
- Modify: `app/backtest/runner.py` (`BacktestResult` dataclass `:125-132`; the `return BacktestResult(...)` at `:763`; the fills-capture site — fills are already collected in `fills_captured`)
- Test: `tests/test_backtest_ui_parity.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_ui_parity.py
from __future__ import annotations
import asyncio
from decimal import Decimal
from app.backtest.runner import run_backtest, BacktestConfig

def _mini_cfg():
    return BacktestConfig(
        instrument="MNQ",
        bars_path="tests/fixtures/bars_parity_mini.csv",
        timeframe="1min",
        enforce_risk_limits=False,
    )

def test_result_carries_fills():
    result = asyncio.run(run_backtest(_mini_cfg()))
    assert hasattr(result, "fills"), "BacktestResult must expose fills"
    assert isinstance(result.fills, list)
```

NOTE: confirm the real `BacktestConfig` field name for the bars path. Inspect `app/backtest/runner.py:92-124`. If the field is not `bars_path`, use the correct name in `_mini_cfg()` (it is referenced by the runner's bar loader). Fix the helper, do not invent a field.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_backtest_ui_parity.py::test_result_carries_fills -v`
Expected: FAIL with `AttributeError: 'BacktestResult' object has no attribute 'fills'`.

- [ ] **Step 3: Add the field and populate it**

In `app/backtest/runner.py`, add to the `BacktestResult` dataclass (after `signals`):
```python
    fills: list[dict] = field(default_factory=list)
```
At the `return BacktestResult(...)` (~`:763`), pass the already-collected fills list:
```python
        fills=fills_captured,
```
(`fills_captured` is the dict list the runner already accumulates — confirm the local variable name near the top of `run_backtest`; it is `fills_captured` per `:605`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_backtest_ui_parity.py::test_result_carries_fills -v`
Expected: PASS.

- [ ] **Step 5: Run the existing backtest suite to confirm no regression**

Run: `.venv/Scripts/python.exe -m pytest tests/test_backtest.py tests/test_backtest_htf.py -q`
Expected: all pass (adding a defaulted field is backward-compatible).

- [ ] **Step 6: Commit**

```bash
git add app/backtest/runner.py tests/test_backtest_ui_parity.py
git commit -m "feat(backtest): expose captured fills on BacktestResult"
```

---

## Task 3: Add `result_to_dict` serializer matching the legacy JSON shape

**Files:**
- Modify: `app/backtest/runner.py` (new module-level function `result_to_dict`)
- Test: `tests/test_backtest_ui_parity.py`

- [ ] **Step 1: Write the failing test**

```python
def test_result_to_dict_has_frontend_contract_keys():
    from app.backtest.runner import run_backtest, result_to_dict
    result = asyncio.run(run_backtest(_mini_cfg()))
    d = result_to_dict(result, id="abc", label="lbl",
                       started_at="2026-06-16T00:00:00Z",
                       completed_at="2026-06-16T00:01:00Z")
    # Load-bearing keys read by server.py and the frontend:
    for key in ("id", "label", "started_at", "completed_at",
                "stats", "trades", "signals", "fills",
                "config", "bars_processed", "rejected_signals"):
        assert key in d, f"missing top-level key: {key}"
    for key in ("trades", "win_rate", "net_pnl"):
        assert key in d["stats"], f"missing stats key: {key}"
    # Must be JSON-serializable (no Decimal/datetime leaking through):
    import json
    json.dumps(d)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_backtest_ui_parity.py::test_result_to_dict_has_frontend_contract_keys -v`
Expected: FAIL with `ImportError: cannot import name 'result_to_dict'`.

- [ ] **Step 3: Implement the serializer**

Add to `app/backtest/runner.py` (module level). Reuse the project's existing JSON-coercion if one exists (search `def _json` / `default=` in the file); otherwise inline a coercer:
```python
import dataclasses

def _json_safe(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj

def result_to_dict(result: BacktestResult, *, id: str, label: str,
                   started_at: str, completed_at: str) -> dict:
    """Serialize a BacktestResult into the dict shape the UI/frontend reads.

    Contract (load-bearing): top-level id/label/started_at/completed_at/stats/
    trades/signals/fills/config/bars_processed/rejected_signals; stats must
    contain trades/win_rate/net_pnl. See docs plan 2026-06-16-ui-backtest-parity.
    """
    stats = _json_safe(dataclasses.asdict(result.stats))
    config = _json_safe(dataclasses.asdict(result.config))
    return {
        "id": id,
        "label": label or result.label,
        "started_at": started_at,
        "completed_at": completed_at,
        "stats": stats,
        "trades": _json_safe(result.trades),
        "signals": _json_safe(result.signals),
        "fills": _json_safe(result.fills),
        "config": config,
        "bars_processed": result.bars_processed,
        "rejected_signals": result.rejected_signals,
    }
```
NOTE: `dataclasses.asdict` on `BacktestConfig` will include `strategy_params` (a pydantic model) — if `asdict` raises or mishandles it, replace `dataclasses.asdict(result.config)` with a manual dict of the scalar fields plus `result.config.strategy_params.model_dump()`. Verify by running the test; fix if it errors.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_backtest_ui_parity.py::test_result_to_dict_has_frontend_contract_keys -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/backtest/runner.py tests/test_backtest_ui_parity.py
git commit -m "feat(backtest): result_to_dict serializer for UI JSON parity"
```

---

## Task 4: Rewire `app/backtest.py` main() to delegate to the canonical runner

**Files:**
- Modify: `app/backtest.py` (delete local `run_backtest` `:164-257`, local `_build_runner` `:61-…`, inline bar loop `:218`; rewrite `main()` arg→`BacktestConfig` mapping `:259-316`)
- Test: `tests/test_backtest_ui_parity.py`

- [ ] **Step 1: Write the failing CLI smoke test**

```python
import json, subprocess, sys, tempfile, os
from pathlib import Path

def test_cli_subprocess_writes_frontend_json(tmp_path):
    out = tmp_path / "bt"
    cmd = [sys.executable, "-m", "app.backtest",
           "--bars", "tests/fixtures/bars_parity_mini.csv",
           "--instrument", "MNQ", "--timeframe", "1min",
           "--out-dir", str(out), "--id", "parity_smoke",
           "--label", "parity smoke"]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       env={**os.environ})
    assert r.returncode == 0, r.stderr
    f = out / "parity_smoke.json"
    assert f.exists(), "subprocess must write <id>.json"
    d = json.loads(f.read_text())
    assert d["id"] == "parity_smoke"
    assert d["label"] == "parity smoke"
    for k in ("trades", "win_rate", "net_pnl"):
        assert k in d["stats"]
```
Run with the venv interpreter — set the test command in Step 2 to use `.venv/Scripts/python.exe` as `sys.executable` by running pytest itself from the venv (then `sys.executable` is the venv python).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_backtest_ui_parity.py::test_cli_subprocess_writes_frontend_json -v`
Expected: FAIL — currently the CLI uses the legacy path; the test may pass on shape by accident, so FIRST make it fail meaningfully by asserting a key the legacy path lacks but the canonical adds (e.g. add `assert "profit_factor" in d["stats"]`, which the canonical `BacktestStats` has but the legacy `compute_stats` may not). Confirm legacy output lacks it before rewiring; if legacy already has it, pick another canonical-only stat field (`expectancy`, `passed_combine`).

- [ ] **Step 3: Rewrite `main()` to build `BacktestConfig` and delegate**

Replace the body of `main()`/`async` runner in `app/backtest.py` so it:
1. Parses the same args (`--config --bars --instrument --timeframe --starting-balance --out-dir --id --label`). Keep the argparse block unchanged.
2. Loads `BotConfig` via `load_bot_config(args.config)`; pull `StrategyParams` from it.
3. Builds a `BacktestConfig`:
```python
from app.backtest.runner import run_backtest, result_to_dict, BacktestConfig
cfg = BacktestConfig(
    instrument=args.instrument,
    bars_path=args.bars,                      # use the real field name from Task 2 Step 1
    timeframe=args.timeframe or "1min",
    starting_balance=Decimal(args.starting_balance),
    strategy_params=bot_cfg.strategy,
    enabled_killzones=bot_cfg.enabled_killzones,
    label=args.label or "",
    # leave risk/commission/etc. at BacktestConfig defaults unless the legacy
    # CLI exposed them (it did not) — defaults match the live runner.
)
```
4. Calls the canonical runner and serializes:
```python
result = await run_backtest(cfg)
d = result_to_dict(result, id=run_id, label=args.label or run_id,
                   started_at=started_at, completed_at=completed_at)
out_path.write_text(json.dumps(d, indent=2))
```
5. Keeps the final log line reading `d["stats"]["trades"|"win_rate"|"net_pnl"]`.
6. **Delete** the now-dead local `run_backtest`, local `_build_runner`, `compute_stats` import if unused, and the inline `for bar in load_bars_csv(...)` loop.

NOTE: confirm `BotConfig` attribute names (`bot_cfg.strategy`, `bot_cfg.enabled_killzones`) against `app/bot_config.py`. Use the real names.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_backtest_ui_parity.py -v`
Expected: all parity tests PASS.

- [ ] **Step 5: Verify no other caller imported the deleted symbols**

Run:
```bash
grep -rnE "from app.backtest import|app\.backtest\.run_backtest|backtest\._build_runner" app/ tests/ scripts/ | grep -v "app.backtest.runner"
```
Expected: no references to the deleted local `run_backtest`/`_build_runner`. If any exist, update them to the canonical module.

- [ ] **Step 6: Commit**

```bash
git add app/backtest.py tests/test_backtest_ui_parity.py
git commit -m "refactor(backtest): UI subprocess delegates to canonical runner.run_backtest"
```

---

## Task 5: End-to-end UI verification (acceptance)

**Files:** none (manual + scripted verification)

- [ ] **Step 1: Run a backtest through the API exactly as the UI does**

Start the backend, then:
```bash
curl -s -X POST localhost:8000/api/backtest/run -H 'Content-Type: application/json' \
  -d '{"bars":"tests/fixtures/bars_parity_mini.csv","instrument":"MNQ","timeframe":"1min","label":"parity-e2e"}'
```
Expected: a JSON response with a `run_id` (or accepted/spawned status). Inspect `app/api/server.py:1363-1482` for the exact request schema (`BacktestRequest`) and match its fields.

- [ ] **Step 2: Confirm the result file renders the load-bearing keys**

Run:
```bash
ls -t backtests/*.json | head -1 | xargs -I{} .venv/Scripts/python.exe -c "import json,sys; d=json.load(open('{}')); print({k:d['stats'][k] for k in ('trades','win_rate','net_pnl')})"
```
Expected: prints the three stats without error.

- [ ] **Step 3: Open the dashboard and confirm the backtest renders**

Per CLAUDE.md Rule 4: load `localhost:5173` (or the built static at `:5175`), open the backtests view, confirm the new run appears with stats and no console errors. If frontend was rebuilt, run `npm run build` in `frontend/` first (per memory: frozen-bars trap).

- [ ] **Step 4: Full backtest suite green**

Run: `.venv/Scripts/python.exe -m pytest tests/test_backtest.py tests/test_backtest_htf.py tests/test_backtest_ui_parity.py -q`
Expected: all pass.

- [ ] **Step 5: Final commit (if any cleanup)**

```bash
git add -A
git commit -m "test(backtest): e2e UI parity verification"
```

---

## Self-Review notes (author)

- **Spec coverage:** delegation (Task 4), JSON-shape parity (Tasks 2-3 + test), UI render (Task 5). Multi-symbol explicitly deferred with a named follow-up plan — covered by scope decision, not silently dropped.
- **Known verification points flagged inline** (not placeholders — they are "confirm the real field name" guards): `BacktestConfig` bars-path field name (Task 2/4), `dataclasses.asdict` behavior on the pydantic `strategy_params` (Task 3), `BotConfig` attribute names (Task 4), `BacktestRequest` schema (Task 5). Each has an exact inspection command/location.
- **Risk:** the legacy `compute_stats` and canonical `BacktestStats` may differ in stat *values* (e.g. commission defaults differ: legacy CLI used `--commission`? it did not; canonical defaults to 0.74/side). This is acceptable — the canonical runner is the faithful one — but note it in the PR so prior UI backtests aren't compared 1:1 to post-refactor ones.
