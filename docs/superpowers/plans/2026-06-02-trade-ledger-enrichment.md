# Trade Ledger Enrichment (grade + slippage) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record each placed trade's setup **grade**, the grader's **reason** string, and entry **slippage** as columns in `trades.csv`, so post-hoc analysis (win-rate by grade, slippage stats) reads from the durable ledger instead of overwrite-prone logs.

**Architecture:** Purely additive to the existing CSV-journaling layer in `app/main.py`. Three new columns are appended to `_TRADES_HEADERS` (end of row = backward-compatible for existing readers). The grade letter + reason are captured into the `_pending_signal_meta` dict inside `pre_place` (read from `signal.setup_grade`); slippage is computed in `_append_fill_csv` for ENTRY rows as `fill_price - signal_entry`. No engine, broker, or risk changes.

**Tech Stack:** Python 3, `csv` stdlib, pytest with `monkeypatch`/`tmp_path` (existing convention in `tests/test_main.py`).

**Scope note:** This is the first of several Stage-1 data-capture plans (see memory `project-monitoring-insights-backlog`). Deliberately OUT of scope here, each its own follow-on plan: `bias_at_entry` (needs engine plumbing to expose `htf_bias` at signal time), the `rejections.csv` ledger (needs runner→engine event plumbing for grader-B / premature-liq / invalidated rejections), and the MFE/MAE tracker (new stateful subsystem).

---

## File Structure

- **Modify** `app/main.py`
  - `_TRADES_HEADERS` (≈ line 325): append `"grade"`, `"grade_reason"`, `"slippage"`.
  - `_make_pre_place` → `pre_place` (≈ line 250): add `grade` + `grade_reason` to the meta dict from `signal.setup_grade`.
  - `_append_fill_csv` (≈ line 352): append the three new values to `row`; compute `slippage` for ENTRY rows.
- **Modify** `tests/test_main.py`: add two tests following the existing `test_append_fill_csv_*` pattern.

No new files.

---

### Task 1: Add grade + reason + slippage to the CSV output

**Files:**
- Modify: `app/main.py` (`_TRADES_HEADERS`, `_append_fill_csv`)
- Test: `tests/test_main.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_main.py` (it already imports `csv`, `Fill`, `datetime`, `timezone`, `Decimal`, `_append_fill_csv`):

```python
def test_append_fill_csv_records_grade_and_slippage(tmp_path: Path, monkeypatch):
    """ENTRY rows carry grade, grade_reason, and computed slippage (fill - entry)."""
    master = tmp_path / "trades.csv"
    daily = tmp_path / "trades_today.csv"
    monkeypatch.setattr("app.main._TRADES_CSV", master)
    monkeypatch.setattr("app.main._daily_csv_path", lambda: daily)
    oid = "OID-1"
    monkeypatch.setattr("app.main._pending_signal_meta", {oid: {
        "signal_entry": "4559.7",
        "grade": "A-",
        "grade_reason": "All: grade A- - momentum=decent, P/D=ok, fib=low (0.91x)",
    }})
    fill = Fill(
        ts=datetime(2026, 6, 2, 10, 14, tzinfo=timezone.utc),
        instrument="MGC", side="short", fill_price=Decimal("4558.1"),
        size=20, is_entry=True, realized_pnl_delta=Decimal("0"),
        broker_order_id=oid,
    )
    _append_fill_csv(fill)
    rows = list(csv.DictReader(master.open(encoding="utf-8")))
    assert rows[0]["grade"] == "A-"
    assert rows[0]["grade_reason"].startswith("All: grade A-")
    # slippage = fill - signal_entry = 4558.1 - 4559.7 = -1.6
    assert float(rows[0]["slippage"]) == pytest.approx(-1.6)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_main.py::test_append_fill_csv_records_grade_and_slippage -v`
Expected: FAIL with `KeyError: 'grade'` (column not in the CSV yet).

- [ ] **Step 3: Add the three header columns**

In `app/main.py`, at the END of the `_TRADES_HEADERS` list (after `"vp_enabled",`):

```python
    "body_atr_multiple", "vp_enabled",
    # Grade + execution quality (ENTRY rows)
    "grade", "grade_reason", "slippage",
]
```

- [ ] **Step 4: Append the three values in `_append_fill_csv`**

In `_append_fill_csv`, locate the `row = [ ... ]` list. After the final `meta.get("vp_enabled", "")` entry, and BEFORE the closing `]`, add:

```python
        meta.get("vp_enabled", ""),
        # Grade + slippage
        meta.get("grade", ""),
        meta.get("grade_reason", ""),
        _entry_slippage(fill, meta),
    ]
```

Then add this module-level helper directly above `_append_fill_csv`:

```python
def _entry_slippage(fill: Fill, meta: dict) -> str:
    """fill_price - signal_entry for ENTRY rows; "" otherwise or if entry unknown.

    Raw signed difference (interpret adversity by side downstream: a SHORT
    filling below its planned entry, or a LONG above, is adverse)."""
    if not fill.is_entry:
        return ""
    entry = meta.get("signal_entry")
    if not entry:
        return ""
    try:
        return str(Decimal(str(fill.fill_price)) - Decimal(str(entry)))
    except Exception:
        return ""
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_main.py::test_append_fill_csv_records_grade_and_slippage -v`
Expected: PASS.

- [ ] **Step 6: Run the existing CSV test to confirm no regression**

Run: `.venv/Scripts/python.exe -m pytest tests/test_main.py -k append_fill_csv -v`
Expected: both `test_append_fill_csv_logs_entry_with_unicode_rationale` and the new test PASS.

- [ ] **Step 7: Commit**

```bash
git add app/main.py tests/test_main.py
git commit -m "feat: record grade, grade_reason, slippage in trades.csv"
```

---

### Task 2: Populate grade + reason into the pre-place meta

**Files:**
- Modify: `app/main.py` (`_make_pre_place` → `pre_place`)
- Test: `tests/test_main.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_main.py` (add `from types import SimpleNamespace` to the imports if absent; `_make_pre_place` is exported from `app.main`):

```python
def test_pre_place_captures_grade_into_meta(monkeypatch):
    """pre_place copies the signal's setup_grade letter + reason into the meta dict."""
    from app.main import _make_pre_place, _pending_signal_meta
    _pending_signal_meta.clear()
    grade = SimpleNamespace(grade="A", reason="All: grade A - momentum=strong, P/D=ok")
    signal = SimpleNamespace(
        instrument="MGC", side="long", entry=Decimal("4556.4"),
        stop=Decimal("4555.5"), target=Decimal("4561.0"), killzone="All",
        sweep_pattern="B_one_bar", sweep_extreme=Decimal("4555.6"),
        fvg_low=Decimal("4555.8"), fvg_high=Decimal("4556.4"),
        rationale="bullish setup", setup_grade=grade,
    )
    pre_place = _make_pre_place(config_path=None)  # config_path=None -> BotConfig() defaults
    asyncio.run(pre_place(signal, 20))
    meta = _pending_signal_meta["MGC"]
    assert meta["grade"] == "A"
    assert meta["grade_reason"] == "All: grade A - momentum=strong, P/D=ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_main.py::test_pre_place_captures_grade_into_meta -v`
Expected: FAIL with `KeyError: 'grade'`.

- [ ] **Step 3: Add grade capture in `pre_place`**

In `_make_pre_place`'s `pre_place`, inside the `_pending_signal_meta[signal.instrument] = { ... }` dict literal, add these two keys after `"vp_enabled": ...,`:

```python
            "vp_enabled":        str(cfg_snap.strategy.vp_enabled),
            "grade":             signal.setup_grade.grade if signal.setup_grade else "",
            "grade_reason":      signal.setup_grade.reason if signal.setup_grade else "",
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_main.py::test_pre_place_captures_grade_into_meta -v`
Expected: PASS.

- [ ] **Step 5: Run the full main test module**

Run: `.venv/Scripts/python.exe -m pytest tests/test_main.py -v`
Expected: all PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_main.py
git commit -m "feat: capture setup grade + reason into pre-place meta for trades.csv"
```

---

### Task 3: Verify end-to-end on the live ledger

**Files:** none (manual verification).

- [ ] **Step 1: Restart the practice bot** using the `run-bot` skill (zombie-safe PID kill + `Tee -Append`), so the running process picks up the new CSV columns.

- [ ] **Step 2: After the next placed trade**, confirm the header and a populated row:

Run: `tr -d '\000' < logs/$(date +%F).log | grep -a "SIGNAL PLACED" | tail -1` (confirm a trade fired), then:
Run: `head -1 trades/trades.csv; grep ENTRY trades/trades.csv | tail -1`
Expected: the header includes `grade,grade_reason,slippage`, and the latest ENTRY row has a non-empty `grade` (e.g. `A-`) and a numeric `slippage`.

- [ ] **Step 3:** If the next trade is far off, this verification can be deferred — the unit tests already prove the row construction. Note in the handoff that live confirmation is pending.

---

## Self-Review

**Spec coverage:** Of the four Stage-1 data items, this plan covers `grade` (+ criteria via the reason string) and `slippage`. `bias_at_entry` and the `rejections.csv` ledger are explicitly deferred (scope note) because they require engine/runner plumbing — they are separate plans, not gaps in *this* plan. MFE/MAE likewise deferred.

**Placeholder scan:** No TBD/TODO; every code change shows full code; tests include real assertions and exact pytest commands with expected outcomes.

**Type consistency:** `_entry_slippage(fill, meta)` is defined in Task 1 and used in the same row literal. Meta keys `"grade"`/`"grade_reason"` are written in Task 2 (`pre_place`) and read in Task 1 (`_append_fill_csv` via `meta.get`) and the tests — consistent names throughout. `signal.setup_grade.grade`/`.reason` match the `SetupGrade` dataclass fields (`app/strategy/grader.py`).

**Note on ordering:** Task 1 is tested with the meta set directly (monkeypatched), so it passes independently of Task 2. Task 2 wires the real producer. Either order works; Task 1 first keeps the CSV-output change isolated.
