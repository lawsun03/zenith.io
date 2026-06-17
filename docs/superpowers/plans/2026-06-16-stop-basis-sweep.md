# Stop-Basis A/B Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a single `stop_basis` selector to the iFVG stop computation so a backtest can A/B the stop anchor — iFVG-edge vs sweep-extreme vs ATR — then run that sweep on 5y Databento bars to validate the deployed sweep-extreme stop (currently applied on n=4, unvalidated).

**Architecture:** The composer computes `signal.stop` at `app/strategy/composer.py:685-713` from a `stop_anchor` chosen by `swing_stop_lookback` (swing vs sweep_extreme), then buffered. We add an additive `stop_basis` field to `StrategyParams` whose **default preserves today's behavior exactly** (sentinel `"default"` → existing lookback logic untouched). New values select a different anchor. `stop_basis` lives on `StrategyParams`, so it is sweepable through the existing `SweepDimension(container="strategy")` path (`app/backtest/runner.py:787`) with no runner changes.

**Tech Stack:** Python 3.12, pydantic `StrategyParams`, pytest, `app/strategy/composer.py`, `app/backtest/runner.py:run_sweep`. Run all Python via `.venv/Scripts/python.exe`.

**Default-off safety:** `stop_basis="default"` must produce byte-identical stops to current `master`. A regression test (Task 3) locks this. Live config never sets `stop_basis` unless deliberately changed.

---

## Task 1: Add `stop_basis` + `atr_stop_mult` to StrategyParams (behavior-preserving defaults)

**Files:**
- Modify: `app/bot_config.py` (`StrategyParams`, near `stop_buffer` at `:44` / `stop_mode` at `:271`)
- Modify: `app/strategy/composer.py` (`ComposerConfig`, near `stop_mode` at `:188`) — mirror the field so the composer reads it
- Test: `tests/test_stop_basis.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stop_basis.py
from decimal import Decimal
from app.bot_config import StrategyParams

def test_stop_basis_defaults_preserve_behavior():
    sp = StrategyParams()
    assert sp.stop_basis == "default"          # sentinel = existing logic
    assert sp.atr_stop_mult == Decimal("1.0")  # only used when stop_basis == "atr"
```

- [ ] **Step 2: Run — verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_stop_basis.py::test_stop_basis_defaults_preserve_behavior -v`
Expected: FAIL — `AttributeError: ... has no attribute 'stop_basis'`.

- [ ] **Step 3: Add the fields**

In `app/bot_config.py` `StrategyParams`:
```python
    # Stop anchor selector for the iFVG path. "default" preserves the existing
    # swing_stop_lookback logic (no behavior change). Other values are for the
    # stop-basis A/B sweep: "ifvg_edge" | "sweep_extreme" | "atr".
    stop_basis: str = "default"
    atr_stop_mult: Decimal = Decimal("1.0")  # ATR multiple; only when stop_basis == "atr"
```
In `app/strategy/composer.py` `ComposerConfig` (the dataclass the composer reads), add the same two fields with identical defaults so `_build_runner`'s `StrategyParams → ComposerConfig` mapping carries them. Then add them to that mapping (search `ComposerConfig(` in `app/strategy/composer.py` and `app/execution/engine.py` / `app/backtest/runner.py` `_build_runner`; wherever `stop_buffer=s.stop_buffer` is set, add `stop_basis=s.stop_basis, atr_stop_mult=s.atr_stop_mult`).

- [ ] **Step 4: Run — verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_stop_basis.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/bot_config.py app/strategy/composer.py tests/test_stop_basis.py
git commit -m "feat(stop): add stop_basis + atr_stop_mult params (default preserves behavior)"
```

---

## Task 2: Branch the composer stop anchor on `stop_basis`

**Files:**
- Modify: `app/strategy/composer.py:685-713` (the long/short `stop_anchor` block)
- Test: `tests/test_stop_basis.py`

- [ ] **Step 1: Write failing tests for each basis (long + short)**

Add to `tests/test_stop_basis.py`. Use the composer's own signal-build path via an existing helper if one exists (search `tests/` for a composer fixture that produces a `Signal`); otherwise unit-test the extracted anchor function from Step 3.

```python
def test_ifvg_edge_anchor_long():
    # long: stop anchored at iFVG zone_low (not the sweep extreme below it)
    # zone_low=100.0, sweep_extreme=99.0, stop_buffer=0.30
    # expect stop == 100.0 - 0.30 == 99.70
    from app.strategy.composer import _stop_anchor_for_basis
    anchor = _stop_anchor_for_basis(
        basis="ifvg_edge", side="long",
        zone_low=Decimal("100.0"), zone_high=Decimal("101.0"),
        sweep_extreme=Decimal("99.0"), swing_anchor=Decimal("98.5"),
        entry=Decimal("101.0"), atr=Decimal("2.0"), atr_mult=Decimal("1.0"),
    )
    assert anchor == Decimal("100.0")

def test_sweep_extreme_anchor_long():
    from app.strategy.composer import _stop_anchor_for_basis
    anchor = _stop_anchor_for_basis(
        basis="sweep_extreme", side="long",
        zone_low=Decimal("100.0"), zone_high=Decimal("101.0"),
        sweep_extreme=Decimal("99.0"), swing_anchor=Decimal("98.5"),
        entry=Decimal("101.0"), atr=Decimal("2.0"), atr_mult=Decimal("1.0"),
    )
    assert anchor == Decimal("99.0")

def test_atr_anchor_long():
    # atr basis: anchor = entry - atr_mult*atr = 101.0 - 1.0*2.0 = 99.0
    from app.strategy.composer import _stop_anchor_for_basis
    anchor = _stop_anchor_for_basis(
        basis="atr", side="long",
        zone_low=Decimal("100.0"), zone_high=Decimal("101.0"),
        sweep_extreme=Decimal("99.0"), swing_anchor=Decimal("98.5"),
        entry=Decimal("101.0"), atr=Decimal("2.0"), atr_mult=Decimal("1.0"),
    )
    assert anchor == Decimal("99.0")
```
(Add the symmetric `_short` variants: ifvg_edge → zone_high; atr → entry + atr_mult*atr.)

- [ ] **Step 2: Run — verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_stop_basis.py -k anchor -v`
Expected: FAIL — `ImportError: cannot import name '_stop_anchor_for_basis'`.

- [ ] **Step 3: Extract + implement the anchor selector**

In `app/strategy/composer.py`, add a pure helper and call it from the long/short blocks:
```python
def _stop_anchor_for_basis(*, basis, side, zone_low, zone_high, sweep_extreme,
                           swing_anchor, entry, atr, atr_mult):
    """Stop ANCHOR (pre-buffer) for the chosen basis. 'default' returns None so
    the caller keeps its existing swing_stop_lookback logic untouched."""
    if basis == "default":
        return None
    if basis == "ifvg_edge":
        return zone_low if side == "long" else zone_high
    if basis == "sweep_extreme":
        return sweep_extreme
    if basis == "atr":
        return (entry - atr_mult * atr) if side == "long" else (entry + atr_mult * atr)
    return None  # unknown basis -> safe fallback to existing logic
```
In the long block (`:685-697`), after `swing_anchor`/`stop_anchor` are computed, override only when a basis is selected:
```python
            _b = _stop_anchor_for_basis(
                basis=cfg.stop_basis, side="long",
                zone_low=zone_low, zone_high=zone_high,
                sweep_extreme=awaiting.sweep.sweep_extreme,
                swing_anchor=(swing_anchor if lookback > 0 and self._bar_lows else None),
                entry=entry, atr=event.atr_at_event, atr_mult=cfg.atr_stop_mult)
            if _b is not None:
                stop_anchor = _b
```
Mirror in the short block (`:700-711`) with `side="short"`. Leave the `max_stop_atr` cap (`:719-729`) as-is — it still applies as a width cap on top of any basis.

- [ ] **Step 4: Run — verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_stop_basis.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/strategy/composer.py tests/test_stop_basis.py
git commit -m "feat(stop): select stop anchor by stop_basis in composer"
```

---

## Task 3: Regression lock — default basis is byte-identical to master

**Files:**
- Test: `tests/test_stop_basis.py`

- [ ] **Step 1: Write the regression test**

Run one short backtest with `stop_basis="default"` and assert the trade stops equal a backtest run with the field absent (i.e. current behavior). Simplest: assert that for the default basis, `_stop_anchor_for_basis(...)` returns `None` for every side, so the existing code path is provably unchanged.

```python
def test_default_basis_is_noop():
    from app.strategy.composer import _stop_anchor_for_basis
    for side in ("long", "short"):
        assert _stop_anchor_for_basis(
            basis="default", side=side,
            zone_low=Decimal("100"), zone_high=Decimal("101"),
            sweep_extreme=Decimal("99"), swing_anchor=Decimal("98"),
            entry=Decimal("101"), atr=Decimal("2"), atr_mult=Decimal("1")) is None
```

- [ ] **Step 2-4:** Run (fails if Task 2 regressed the sentinel), implement nothing new, verify pass, commit.

```bash
.venv/Scripts/python.exe -m pytest tests/test_stop_basis.py -q
git commit -am "test(stop): lock default basis as no-op"
```

- [ ] **Step 5: Full suite green**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all pass (no behavior change on default).

---

## Task 4: Run the 3-way sweep on 5y bars

**Files:** none (a run) — optionally `scripts/run_stop_basis_sweep.py`

- [ ] **Step 1: Confirm the sweep dimension wiring**

`SweepDimension(target="stop_basis", values=["ifvg_edge","sweep_extreme","atr"], container="strategy")` is applied by `_apply_sweep_dim` (`app/backtest/runner.py:787`) via `strategy_params.model_copy(update={"stop_basis": value})`. No runner change needed. (Optionally add `atr_stop_mult` as a second dimension `[Decimal("0.75"), Decimal("1.0"), Decimal("1.5")]` to tune the ATR variant.)

- [ ] **Step 2: Run the sweep**

Use `run_sweep(base, dims, bars_factory)` against `bars/bars_MNQ_dbv_2021_2026.csv` (5y Databento, on disk), `enforce_risk_limits=False` for exploration. Base config = the deployed MNQ 5min winner (see memory `project_deployed_config`). Write a thin `scripts/run_stop_basis_sweep.py` mirroring an existing `scripts/run_b*` sweep script if a template helps.

- [ ] **Step 3: Report**

Compare profit factor / net P&L / max drawdown / trade count across the three bases (and ATR multiples). The deployed choice is `sweep_extreme`; the sweep either confirms it or flags a better anchor. Record the verdict in memory `project_monitoring_insights_backlog` (the "stop-basis sweep" cross-ref) and `project_mnq5min_walkforward`.

---

## Self-Review notes
- **Spec coverage:** three bases (Task 2), default-safety (Tasks 1+3), sweep run (Task 4). ATR basis uses `event.atr_at_event` (already in scope at composer.py:719) + new `atr_stop_mult`.
- **Verify-points (not placeholders):** exact `ComposerConfig(...)` construction sites for the field mapping (Task 1 Step 3) and any existing composer Signal-build test fixture (Task 2 Step 1) — both have grep instructions.
- **Conflict guard (Rule 7):** extends the existing stop mechanism via one additive selector rather than adding a parallel knob; `max_stop_atr` cap is intentionally preserved as an orthogonal width cap.
- **Out of scope:** wiring `stop_basis` into `armed_zone.arm()` for live trading. The sweep validates the basis in backtest first; only after a winner is chosen does the live armed-entry path need the same selector (follow-up).
