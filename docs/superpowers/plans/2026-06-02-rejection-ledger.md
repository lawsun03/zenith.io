# Rejection Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write every *rejected* setup to a durable `rejections.csv` ledger — both engine-surfaced denials (`vp_filter`, `htf_bias`) and runner-internal rejections (grader-B filter, premature-liquidity cancel, zone invalidate) — so "missed trades" analysis reads structured data instead of overwrite-prone logs.

**Architecture:** Two layers feed one writer. **Layer 1 (engine denials):** `_apply_confluence` already returns a reason and `journal_signal`'s `else` branch already has the full signal — write a rejection row there. **Layer 2 (runner-internal):** add a `RejectInfo` value + a `StrategyRunner.last_reject` attribute set at the three internal reject sites and cleared each bar; `ExecutionEngine._handle_bar` reads it after `on_bar` (when no signal was produced) and invokes a new `on_reject` callback wired in `main.py` to the same writer. Mirrors the existing `trades.csv` / `on_signal` patterns.

**Tech Stack:** Python 3, `csv` stdlib, dataclasses, pytest (`monkeypatch`/`tmp_path`).

**Scope:** Captures all five rejection reasons. MFE/MAE on rejections (did the missed setup go on to profit?) is a SEPARATE follow-on plan (the MFE/MAE tracker) — this plan only records that a rejection happened and its would-be levels.

---

## File Structure

- **Modify** `app/main.py`: add `_REJECTIONS_CSV`, `_REJECTIONS_HEADERS`, `_daily_rejections_path()`, `_build_rejection_row(...)`, `_append_rejection_csv(...)`; call it from `journal_signal`'s `else` branch (Layer 1) and from a new `on_reject` callback passed to `ExecutionEngine` (Layer 2).
- **Modify** `app/execution/engine.py`: add `RejectInfo` dataclass; add `StrategyRunner.last_reject: RejectInfo | None` (set at 3 sites, cleared at top of `on_bar`); add `ExecutionEngine.on_reject` callback + invoke it in `_handle_bar`.
- **Test:** `tests/test_main.py` (writer + row), `tests/test_engine.py` (runner sets `last_reject`; engine invokes `on_reject`).

---

### Task 1: Rejection CSV writer (Layer 1 — engine denials)

**Files:** Modify `app/main.py`; Test `tests/test_main.py`

- [ ] **Step 1: Write the failing test**

```python
def test_append_rejection_csv_writes_row(tmp_path: Path, monkeypatch):
    from app.main import _append_rejection_csv
    master = tmp_path / "rejections.csv"
    daily = tmp_path / "rejections_today.csv"
    monkeypatch.setattr("app.main._REJECTIONS_CSV", master)
    monkeypatch.setattr("app.main._daily_rejections_path", lambda: daily)
    _append_rejection_csv(
        ts="2026-06-02T12:19:00+00:00", instrument="MGC", side="long",
        reason="vp_filter", grade="A-", entry="4556.4", stop="4555.5",
        target="4561.0", killzone="All", rationale="bullish", source="engine",
    )
    rows = list(csv.DictReader(master.open(encoding="utf-8")))
    assert rows[0]["reason"] == "vp_filter"
    assert rows[0]["side"] == "long"
    assert rows[0]["entry"] == "4556.4"
    assert rows[0]["source"] == "engine"
```

- [ ] **Step 2: Run to verify FAIL**

Run: `.venv/Scripts/python.exe -m pytest tests/test_main.py::test_append_rejection_csv_writes_row -v`
Expected: FAIL (`ImportError: cannot import name '_append_rejection_csv'`).

- [ ] **Step 3: Implement writer in `app/main.py`** (place after the `_append_fill_csv` block)

```python
_REJECTIONS_CSV = Path("trades/rejections.csv")
_REJECTIONS_HEADERS = [
    "ts", "instrument", "side", "reason", "grade",
    "entry", "stop", "target", "killzone", "rationale", "source",
]


def _daily_rejections_path() -> Path:
    ct_date = datetime.now(_CT).strftime("%Y-%m-%d")
    return Path("trades") / f"rejections_{ct_date}.csv"


def _append_rejection_csv(
    *, ts: str, instrument: str, side: str, reason: str, grade: str = "",
    entry: str = "", stop: str = "", target: str = "", killzone: str = "",
    rationale: str = "", source: str = "",
) -> None:
    """Append one rejected/missed setup. source = 'engine' (vp/bias deny) or 'runner'."""
    row = [ts, instrument, side, reason, grade, entry, stop, target,
           killzone, rationale, source]
    for path in (_REJECTIONS_CSV, _daily_rejections_path()):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_header = not path.exists()
            with path.open("a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if write_header:
                    w.writerow(_REJECTIONS_HEADERS)
                w.writerow(row)
        except Exception:
            log.exception("_append_rejection_csv failed for %s", path)
```

- [ ] **Step 4: Run to verify PASS**

Run: `.venv/Scripts/python.exe -m pytest tests/test_main.py::test_append_rejection_csv_writes_row -v`
Expected: PASS.

- [ ] **Step 5: Wire Layer 1 into `journal_signal`'s `else` branch** (`app/main.py` ~line 320)

Replace the `else:` block that logs `SIGNAL DENIED` with:

```python
        else:
            log.info(
                "SIGNAL DENIED  %s  reason=%s | %s",
                signal.side.upper(), outcome.reason, signal.rationale,
            )
            _g = signal.setup_grade
            _append_rejection_csv(
                ts=datetime.now(timezone.utc).isoformat(),
                instrument=signal.instrument, side=signal.side,
                reason=outcome.reason or "", grade=(_g.grade if _g else ""),
                entry=str(signal.entry), stop=str(signal.stop),
                target=str(signal.target), killzone=signal.killzone or "",
                rationale=signal.rationale or "", source="engine",
            )
```

- [ ] **Step 6: Run full `tests/test_main.py`** — Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add app/main.py tests/test_main.py
git commit -m "feat: rejection ledger - capture engine vp/bias denials to rejections.csv"
```

---

### Task 2: Runner records its internal rejections (`last_reject`)

**Files:** Modify `app/execution/engine.py`; Test `tests/test_engine.py`

- [ ] **Step 1: Write the failing test** (in `tests/test_engine.py`; follow that file's existing StrategyRunner construction — reuse a helper/fixture there)

```python
def test_runner_records_grader_b_rejection():
    """A grade that does not pass sets runner.last_reject with reason 'grader_<G>'."""
    runner = _make_test_runner()  # existing helper in test_engine.py; builds a StrategyRunner
    # Feed bars that produce a displacement candidate the grader fails (B).
    # (Reuse the existing grader-B scenario already exercised in test_engine.py / test_grader.py.)
    for bar in _grader_b_scenario_bars():
        runner.on_bar(bar)
    assert runner.last_reject is not None
    assert runner.last_reject.reason.startswith("grader_")
    assert runner.last_reject.side in ("long", "short")
```

> NOTE for the implementer: `test_engine.py` already constructs `StrategyRunner` and drives bars; reuse its existing fixtures/helpers rather than rebuilding. If no grader-B scenario helper exists, lift the candidate/grade setup from `tests/test_grader.py`.

- [ ] **Step 2: Run to verify FAIL** — `AttributeError: 'StrategyRunner' object has no attribute 'last_reject'`.

- [ ] **Step 3: Add `RejectInfo` + `last_reject`** in `app/execution/engine.py`

Near the top (with the other dataclasses):

```python
@dataclass
class RejectInfo:
    reason: str
    side: str
    entry: Decimal | None
    stop: Decimal | None
    target: Decimal | None
    grade: str
    killzone: str
    rationale: str
```

In `StrategyRunner.__init__` (or its dataclass field defaults), add:

```python
        self.last_reject: RejectInfo | None = None
```

At the TOP of `StrategyRunner.on_bar`, after the news/session early-returns, clear it:

```python
        self.last_reject = None
```

At the **grader-B** site (the `else` after `if grade.passes:`), set it:

```python
                    else:
                        log.info(
                            "Signal filtered by grader: %s — %s",
                            grade.grade, grade.reason,
                        )
                        self.last_reject = RejectInfo(
                            reason=f"grader_{grade.grade}", side=candidate.side,
                            entry=candidate.entry, stop=candidate.stop,
                            target=candidate.target, grade=grade.grade,
                            killzone=candidate.killzone or "",
                            rationale=candidate.rationale or "",
                        )
```

At the **premature-liquidity** sites (both long and short cancel branches), after `self._pending_signal = None`, set it (use the captured `zone` + the pending signal's grade/rationale; the pending signal is still in scope before the `None` assignment — capture first):

```python
                if zone.side == "long" and bar.high >= zone.tp1_price:
                    log.info("Premature liquidity: TP1 %s hit before long entry — cancelling armed zone", zone.tp1_price)
                    _ps = self._pending_signal
                    self.armed_tracker.cancel()
                    self._pending_signal = None
                    self.last_reject = RejectInfo(
                        reason="premature_liquidity", side="long",
                        entry=zone.entry_price, stop=zone.stop_price,
                        target=zone.tp1_price,
                        grade=(_ps.setup_grade.grade if _ps and _ps.setup_grade else ""),
                        killzone=(_ps.killzone if _ps else "") or "",
                        rationale=(_ps.rationale if _ps else "") or "",
                    )
```

(Mirror the same for the `short` branch with `side="short"`.)

At the **invalidate** site (`elif status == "invalidated":`), set it:

```python
            elif status == "invalidated":
                log.info("Armed zone invalidated — pending signal discarded")
                _ps = self._pending_signal
                self._pending_signal = None
                self.last_reject = RejectInfo(
                    reason="invalidated", side=(_ps.side if _ps else ""),
                    entry=(_ps.entry if _ps else None), stop=(_ps.stop if _ps else None),
                    target=(_ps.target if _ps else None),
                    grade=(_ps.setup_grade.grade if _ps and _ps.setup_grade else ""),
                    killzone=(_ps.killzone if _ps else "") or "",
                    rationale=(_ps.rationale if _ps else "") or "",
                )
```

- [ ] **Step 4: Run to verify PASS** — the grader-B test passes.

- [ ] **Step 5: Run `tests/test_engine.py` + `tests/test_armed_zone*.py`** — Expected: all PASS (no regression in arming/fill behavior).

- [ ] **Step 6: Commit**

```bash
git add app/execution/engine.py tests/test_engine.py
git commit -m "feat: rejection ledger - StrategyRunner records last_reject at internal reject sites"
```

---

### Task 3: Engine surfaces runner rejections via `on_reject` (Layer 2 wiring)

**Files:** Modify `app/execution/engine.py`, `app/main.py`; Test `tests/test_engine.py`

- [ ] **Step 1: Write the failing test** (engine invokes `on_reject` when a bar yields no signal but the runner recorded a reject)

```python
@pytest.mark.asyncio
async def test_engine_invokes_on_reject(monkeypatch):
    captured = []
    engine = _make_test_engine(on_reject=lambda info, instrument: captured.append((instrument, info.reason)))
    # Drive a grader-B bar sequence through engine._handle_bar (no signal placed).
    for bar in _grader_b_scenario_bars():
        await engine._handle_bar(bar)
    assert any(reason.startswith("grader_") for _, reason in captured)
```

> NOTE: reuse `test_engine.py`'s existing engine builder; add an `on_reject` kwarg path to it.

- [ ] **Step 2: Run to verify FAIL** — `on_reject` not accepted / never called.

- [ ] **Step 3: Add `on_reject` to `ExecutionEngine`**

In `ExecutionEngine.__init__`, add parameter `on_reject: Callable[[RejectInfo, str], Awaitable[None]] | None = None` and store `self._on_reject = on_reject`.

In `_handle_bar`, replace the `if signal is None or is_stale:` early-return so a runner reject still fires the callback:

```python
        if signal is None:
            rej = getattr(runner, "last_reject", None)
            if rej is not None and self._on_reject is not None and not self._replay_mode:
                try:
                    await self._on_reject(rej, runner.instrument)
                except Exception:
                    log.exception("on_reject callback raised")
            return
        if is_stale:
            # ... (existing stale-bar handling unchanged) ...
```

(Keep the existing armed-fill/stale WARNING logic that follows; only the `signal is None` branch gains the reject hook.)

- [ ] **Step 4: Run to verify PASS**.

- [ ] **Step 5: Wire `on_reject` in `app/main.py`**

Add a builder next to `_make_signal_journaler`:

```python
def _make_reject_journaler():
    async def on_reject(info, instrument: str) -> None:
        _append_rejection_csv(
            ts=datetime.now(timezone.utc).isoformat(), instrument=instrument,
            side=info.side, reason=info.reason, grade=info.grade,
            entry=str(info.entry) if info.entry is not None else "",
            stop=str(info.stop) if info.stop is not None else "",
            target=str(info.target) if info.target is not None else "",
            killzone=info.killzone, rationale=info.rationale, source="runner",
        )
    return on_reject
```

In the `ExecutionEngine(...)` construction (~line 1083), pass `on_reject=_make_reject_journaler()`.

- [ ] **Step 6: Run `tests/test_engine.py` + `tests/test_main.py`** — Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add app/execution/engine.py app/main.py tests/test_engine.py
git commit -m "feat: rejection ledger - engine on_reject surfaces runner rejections to rejections.csv"
```

---

### Task 4: Deploy + live verify

- [ ] **Step 1:** Restart the practice bot (`run-bot` skill — zombie-safe PID kill + `Tee -Append`).
- [ ] **Step 2:** After activity, confirm rows: `head -1 trades/rejections.csv; tail -3 trades/rejections.csv` — expect `reason` values among `vp_filter`/`htf_bias`/`grader_B`/`premature_liquidity`/`invalidated` and `source` in `engine`/`runner`.
- [ ] **Step 3:** If quiet, note live verification deferred — unit/integration tests already prove both layers.

---

## Self-Review

**Spec coverage:** All five reject reasons covered — `vp_filter`/`htf_bias` (Task 1, source=engine), `grader_<G>`/`premature_liquidity`/`invalidated` (Tasks 2–3, source=runner). MFE/MAE on rejections explicitly deferred (separate plan).

**Placeholder scan:** Real code in every implementation step. The two test-helper NOTEs (`_make_test_runner`, `_grader_b_scenario_bars`) point the implementer at existing `test_engine.py`/`test_grader.py` fixtures rather than inventing them — acceptable because those files already construct these objects; the implementer reuses them. Every assertion and command is concrete.

**Type consistency:** `RejectInfo` fields (reason, side, entry, stop, target, grade, killzone, rationale) defined in Task 2 are read identically in Task 3's `on_reject` and `_make_reject_journaler`. `_append_rejection_csv` keyword args match between Task 1's definition, Task 1 Step 5 (engine layer), and Task 3 Step 5 (runner layer). `last_reject` set in Task 2, read in Task 3.

**Ordering:** Task 1 is independently shippable (engine denials only). Tasks 2→3 add the runner layer. Each task ends green + committed.
