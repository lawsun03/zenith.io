# A-F Weighted Scorecard Grading — Design Spec
**Date:** 2026-06-08  
**Status:** Approved for implementation

---

## Problem

The current A+/A/A-/B/B- grading system collapses most live signals into A-, masking large quality differences. On 2026-06-08 every executed trade graded A- yet scores ranged from fib=0.63× (high failure risk) to fib=1.00× (more reliable). The grade provided no signal about which A- setups were worth trading. The soft DLL buffer lockout was triggered entirely by weak A- trades; stronger setups that fired later in the day were blocked.

---

## Goal

Replace the 5-value grade system with a 0–100 numeric score that maps to A/B/C/D/F. No execution behavior changes in this release — `passes` logic is identical to today. The score and letter grade are for analysis, logging, CSV export, and future filter tuning.

---

## Scoring Criteria

All criteria are evaluated independently. Score is the sum; letter grade is derived from score.

| # | Criterion | Max | Breakdown |
|---|-----------|-----|-----------|
| 1 | Fib extension | 30 | ≥1.5× → 30 · 1.0–1.49× → 15 · <1.0× → 0 |
| 2 | Premium/Discount | 20 | Both session + HTF midpoints aligned → 20 · else 0 |
| 3 | Delivery FVG | 20 | Correct side + in P/D → 20 · correct side only → 10 · absent → 0 |
| 4 | Momentum strength | 15 | strong (body_to_atr ≥1.5 AND body_to_range ≥0.6) → 15 · decent → 7 · weak → 0 |
| 5 | BPR confluence | 10 | Present → 10 · absent → 0 |
| 6 | Target clarity | 5 | HTF swing or session extreme aligned → 5 · else 0 |

**Total possible: 100**

### Grade thresholds

| Grade | Score range | Meaning |
|-------|-------------|---------|
| A | 75–100 | All or nearly all criteria met; highest-confidence setups |
| B | 55–74 | Strong criteria met; missing one major factor |
| C | 35–54 | Marginal; passes filters but structurally weak |
| D | 15–34 | Poor; passes hard gates only by thin margin |
| F | 0–14 | Fails most criteria; noise |

### Calibration against 2026-06-08 trades

| Setup type | Fib | P/D | Delivery | Momentum | BPR | Target | Score | Grade |
|------------|-----|-----|----------|----------|-----|--------|-------|-------|
| Typical loser (e.g. 04:06 MES) | 0.79× | off | no | decent | no | yes | 12 | F |
| Best morning setup (04:53 MES) | 1.00× | off | no | decent | yes | yes | 37 | D |
| Current "A" equivalent | 1.2× | ok | no | strong | no | yes | 55 | B |
| Current "A+" equivalent | 1.6× | ok | yes+PD | strong | yes | yes | 100 | A |

This confirms the design goal: the losing morning setups correctly grade F/D, not A.

---

## Code Changes

### `app/strategy/grader.py`

**`SetupGrade` dataclass:**
- `grade` type: `Literal["A+", "A", "A-", "B", "B-"]` → `Literal["A", "B", "C", "D", "F"]`
- Add `score: int` field (0–100)
- All other fields unchanged

**`SetupGrader` methods:**
- Add `_compute_score(momentum_quality, pd_ok, delivery, delivery_in_pd, bpr, target_clear, fib_ext) -> int` — pure function, no side effects
- Add `_score_to_grade(score: int) -> Literal["A","B","C","D","F"]` — static method
- `score()` — existing decision tree unchanged for `passes`. After all criteria evaluated, call `_compute_score()` + `_score_to_grade()` to populate new fields
- `_make_grade()` helper — updated to accept and propagate score
- `reason` string updated: `"NY AM: grade D (42) — fib=low (0.81x), P/D=off, ..."`

**`passes` logic: no change.** The existing decision tree still governs whether a signal executes. Score is computed alongside, not instead of, the binary gate.

### `app/execution/engine.py`

- `grade.grade` used only in log strings and rejection key: `reason=f"grader_{grade.grade}"`. No logic changes required — new letter values ("grader_D", "grader_F") are valid.
- No comparison against specific grade strings anywhere in this file.

### `app/api/journal.py`

- Reads `grade.grade` for journal output. Works with new letter values as-is.

### CSV schema

**`trades/trades.csv` and `trades/trades_YYYY-MM-DD.csv`:**
- `grade` column: values change from `"A-"`/`"A"`/`"A+"` to `"A"`/`"B"`/`"C"`/`"D"`/`"F"`
- Add `score` column (int, 0–100) appended after `grade_reason`
- Historical rows before this change retain old `"A-"` style values; daily files split cleanly at upgrade date

**`trades/rejections.csv` and `trades/rejections_YYYY-MM-DD.csv`:**
- Same: `grade` column values change, `score` column added

**`trades/excursions.csv`:** No grade column — no change.

### Frontend

**Grade badge** (signal panel, rejections view, any grade display):
- A → `text-good` (green)
- B → `text-accent` (blue/teal)
- C → `text-warn` (amber)
- D → `text-danger/70` (muted red)
- F → `text-danger` (red)
- Score shown as subtitle where layout allows (e.g. `"D · 42"`)

### Tests

- `tests/test_engine.py` — update `grade.grade` string assertions from `"A-"`/`"B"` etc. to new letter values
- `tests/test_backtest_runner.py` — `test_reconstruct_trades_includes_grade` has `"grade": "A+"` hardcoded in fixture and assertion; update to a valid new letter (e.g. `"A"`)
- `tests/test_partial_exit.py`, `tests/test_reconciler.py` — check for any grade references and update
- `passes` assertions: unchanged
- Add new tests: `_compute_score()` with known inputs, `_score_to_grade()` boundary values

### Engine signal dataclass

`engine.py` line ~112 has a signal/fill dataclass with `grade: str`. Already loosely typed — no change required; new letter values are valid strings.

---

## What Does Not Change

- `passes` boolean logic — identical execution behavior, no trades added or removed
- Hard gates: momentum <1.0× forces B- (still `passes=False`), overlapping FVGs forces B (`passes=False`), no sweep + no delivery forces B (`passes=False`)
- `BotConfig`, risk state, broker, reconciler — untouched
- Backtest runner — uses `grade.passes` only
- `soft_buffer`, MLL protection — untouched

---

## Out of Scope

- Changing the `passes` threshold based on the new grades (deferred; pending analysis of score distribution in live trading)
- Adding `min_grade` or `min_score` config fields (deferred)
- Retroactively re-scoring historical CSV rows

---

## Success Criteria

1. `npm run build` clean, no TS errors
2. `pytest` passes (adjusted assertions)
3. Live log shows `grade D (42)` style strings on next bot run
4. `trades/trades.csv` new rows contain `score` column with integer values
5. Dashboard grade badges display correct color per letter
