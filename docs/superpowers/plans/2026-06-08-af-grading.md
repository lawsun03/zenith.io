# A-F Weighted Scorecard Grading — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the A+/A/A-/B/B- grade system with a 0–100 weighted score that maps to A/B/C/D/F, with no change to `passes` logic or execution behavior.

**Architecture:** `_compute_score()` and `_score_to_grade()` are added to `SetupGrader` as pure functions. `SetupGrade.grade` type changes; `SetupGrade.score` is added. `journaling.py` appends `score` to CSV rows. Frontend grade styles updated for the 5 new letters.

**Tech Stack:** Python dataclasses, pytest, React/TypeScript, Tailwind CSS

---

## File Map

| File | Change |
|------|--------|
| `app/strategy/grader.py` | Core: new `score` field, `_compute_score()`, `_score_to_grade()`, updated grade type, updated reason string |
| `app/journaling.py` | Add `score` to `_TRADES_HEADERS`, `_pending_signal_meta`, and row list |
| `tests/test_grader_score.py` | New: unit tests for `_compute_score()` and `_score_to_grade()` |
| `tests/test_backtest_runner.py` | Update `"A+"` fixture to `"A"` |
| `frontend/src/components/BacktestsPage.tsx` | Update `GRADE_TIERS` and `GRADE_STYLES` for A/B/C/D/F |
| `frontend/src/types.ts` | Update `grade` comment in `StrategyStatePayload` |

---

## Task 1: Unit tests for score computation

**Files:**
- Create: `tests/test_grader_score.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_grader_score.py
from decimal import Decimal
import pytest
from app.strategy.grader import SetupGrader

@pytest.fixture
def grader():
    return SetupGrader()

# --- _score_to_grade ---

def test_score_to_grade_a(grader):
    assert grader._score_to_grade(75) == "A"
    assert grader._score_to_grade(100) == "A"

def test_score_to_grade_b(grader):
    assert grader._score_to_grade(55) == "B"
    assert grader._score_to_grade(74) == "B"

def test_score_to_grade_c(grader):
    assert grader._score_to_grade(35) == "C"
    assert grader._score_to_grade(54) == "C"

def test_score_to_grade_d(grader):
    assert grader._score_to_grade(15) == "D"
    assert grader._score_to_grade(34) == "D"

def test_score_to_grade_f(grader):
    assert grader._score_to_grade(0) == "F"
    assert grader._score_to_grade(14) == "F"

# --- _compute_score ---

def test_compute_score_all_zero(grader):
    # weak momentum, no P/D, no delivery, no BPR, no target
    score = grader._compute_score(
        momentum_quality="weak",
        pd_ok=False,
        delivery=False,
        delivery_in_pd=False,
        bpr=False,
        target_clear=False,
        fib_ext=Decimal("0.5"),
    )
    assert score == 0

def test_compute_score_decent_momentum_only(grader):
    score = grader._compute_score(
        momentum_quality="decent",
        pd_ok=False,
        delivery=False,
        delivery_in_pd=False,
        bpr=False,
        target_clear=False,
        fib_ext=Decimal("0.5"),
    )
    assert score == 7  # momentum decent only

def test_compute_score_fib_partial(grader):
    score = grader._compute_score(
        momentum_quality="decent",
        pd_ok=False,
        delivery=False,
        delivery_in_pd=False,
        bpr=False,
        target_clear=True,
        fib_ext=Decimal("1.2"),
    )
    assert score == 7 + 5 + 15  # momentum=7, target=5, fib partial=15

def test_compute_score_max(grader):
    score = grader._compute_score(
        momentum_quality="strong",
        pd_ok=True,
        delivery=True,
        delivery_in_pd=True,
        bpr=True,
        target_clear=True,
        fib_ext=Decimal("1.5"),
    )
    assert score == 100  # 30+20+20+15+10+5

def test_compute_score_delivery_without_pd(grader):
    # delivery correct side but not in P/D = 10 pts
    score = grader._compute_score(
        momentum_quality="weak",
        pd_ok=False,
        delivery=True,
        delivery_in_pd=False,
        bpr=False,
        target_clear=False,
        fib_ext=Decimal("0.5"),
    )
    assert score == 10

def test_compute_score_typical_loser(grader):
    # Today's 2026-06-08 worst setup: fib=0.81x, P/D=off, no delivery, decent, no BPR, target=yes
    score = grader._compute_score(
        momentum_quality="decent",
        pd_ok=False,
        delivery=False,
        delivery_in_pd=False,
        bpr=False,
        target_clear=True,
        fib_ext=Decimal("0.81"),
    )
    assert score == 12  # momentum=7, target=5

def test_score_to_grade_typical_loser_is_f(grader):
    assert grader._score_to_grade(12) == "F"
```

- [ ] **Step 2: Run tests to confirm they fail**

```
cd C:\Users\Lawrence\Documents\topstep-trader-bot\topstep-bot
python -m pytest tests/test_grader_score.py -v 2>&1 | head -30
```

Expected: `AttributeError: 'SetupGrader' object has no attribute '_score_to_grade'` or similar for all tests.

- [ ] **Step 3: Commit the failing tests**

```
git add tests/test_grader_score.py
git commit -m "test(grader): failing tests for _compute_score and _score_to_grade"
```

---

## Task 2: Implement `_compute_score` and `_score_to_grade`

**Files:**
- Modify: `app/strategy/grader.py`

- [ ] **Step 1: Add `score` field to `SetupGrade` and update grade type**

In `app/strategy/grader.py`, change the `SetupGrade` dataclass:

```python
@dataclass(frozen=True)
class SetupGrade:
    """Result of grading one signal candidate against Dodgy's 5 criteria."""
    grade: Literal["A", "B", "C", "D", "F"]
    score: int                              # 0-100 weighted scorecard
    passes: bool                            # True if grade >= A- (unchanged logic)
    has_delivery_fvg: bool
    delivery_fvg_side: str | None
    delivery_fvg_in_pd: bool
    premium_discount_ok: bool
    target_clear: bool
    fvg_singular: bool
    singularity_timeframe: str
    momentum_quality: MomentumQuality
    bpr_confluence: bool
    bpr_timeframe: str | None
    recent_sweep_ok: bool
    fib_displacement_ok: bool
    fib_extension: Decimal
    ce_respected: bool
    reason: str
```

- [ ] **Step 2: Add `_compute_score` and `_score_to_grade` to `SetupGrader`**

Add these two methods to the `SetupGrader` class (place after `_check_fib_displacement`, before `_make_grade`):

```python
def _compute_score(
    self,
    momentum_quality: MomentumQuality,
    pd_ok: bool,
    delivery: bool,
    delivery_in_pd: bool,
    bpr: bool,
    target_clear: bool,
    fib_ext: Decimal,
) -> int:
    """Weighted scorecard 0-100. Independent of passes logic."""
    score = 0
    # Fib extension (0-30)
    if fib_ext >= Decimal("1.5"):
        score += 30
    elif fib_ext >= Decimal("1.0"):
        score += 15
    # P/D (0-20)
    if pd_ok:
        score += 20
    # Delivery FVG (0-20): correct side + in P/D = 20, correct side only = 10
    if delivery:
        score += 20 if delivery_in_pd else 10
    # Momentum (0-15)
    if momentum_quality == "strong":
        score += 15
    elif momentum_quality == "decent":
        score += 7
    # BPR (0-10)
    if bpr:
        score += 10
    # Target clarity (0-5)
    if target_clear:
        score += 5
    return score

@staticmethod
def _score_to_grade(score: int) -> Literal["A", "B", "C", "D", "F"]:
    if score >= 75:
        return "A"
    if score >= 55:
        return "B"
    if score >= 35:
        return "C"
    if score >= 15:
        return "D"
    return "F"
```

- [ ] **Step 3: Run unit tests to confirm they pass**

```
python -m pytest tests/test_grader_score.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 4: Commit**

```
git add app/strategy/grader.py tests/test_grader_score.py
git commit -m "feat(grader): add _compute_score and _score_to_grade (A-F weighted scorecard)"
```

---

## Task 3: Wire score into `score()` and `_make_grade()`

**Files:**
- Modify: `app/strategy/grader.py`

- [ ] **Step 1: Update `_make_grade` to compute and propagate score**

Replace the `_make_grade` method signature and body with:

```python
def _make_grade(
    self,
    grade_str: str,           # ignored — derived from score
    passes: bool,
    momentum_quality: MomentumQuality = "decent",
    target_clear: bool = True,
    fvg_singular: bool = True,
    singularity_timeframe: str = "1min",
    bpr_confluence: bool = False,
    bpr_timeframe: str | None = None,
    reason: str = "",
    signal: "Signal | None" = None,
    disp: "DisplacementEvent | None" = None,
    bars_since_sweep: int = 0,
    sweep_window_bars: int = 10,
    min_displacement_mult: Decimal = Decimal("1.0"),
) -> SetupGrade:
    """Helper for early-exit grade construction with safe defaults."""
    recent_sweep_ok = bars_since_sweep <= sweep_window_bars
    fib_ok = False
    fib_ext = Decimal("0")
    if disp is not None and signal is not None:
        fib_ok, fib_ext = self._check_fib_displacement(
            signal, disp, min_displacement_mult
        )
    score = self._compute_score(
        momentum_quality=momentum_quality,
        pd_ok=False,
        delivery=False,
        delivery_in_pd=False,
        bpr=bpr_confluence,
        target_clear=target_clear,
        fib_ext=fib_ext,
    )
    letter = self._score_to_grade(score)
    return SetupGrade(
        grade=letter, score=score, passes=passes,
        has_delivery_fvg=False,
        delivery_fvg_side=None,
        delivery_fvg_in_pd=False,
        premium_discount_ok=False,
        target_clear=target_clear,
        fvg_singular=fvg_singular,
        singularity_timeframe=singularity_timeframe,
        momentum_quality=momentum_quality,
        bpr_confluence=bpr_confluence,
        bpr_timeframe=bpr_timeframe,
        recent_sweep_ok=recent_sweep_ok,
        fib_displacement_ok=fib_ok,
        fib_extension=fib_ext,
        ce_respected=False,
        reason=reason,
    )
```

- [ ] **Step 2: Update the main `score()` method to wire score and new reason string**

In the `score()` method, find the grade assignment block (lines ~230–260) and the `SetupGrade(...)` constructor call. Replace the reason string and `SetupGrade` constructor:

The reason string (replace the existing `reason = (...)` assignment):
```python
score_val = self._compute_score(
    momentum_quality=momentum_quality,
    pd_ok=pd_ok,
    delivery=delivery,
    delivery_in_pd=delivery_in_pd,
    bpr=bpr,
    target_clear=target_clear,
    fib_ext=fib_ext,
)
grade_letter = self._score_to_grade(score_val)

reason = (
    f"{signal.killzone}: grade {grade_letter} ({score_val}) — "
    f"momentum={momentum_quality}, P/D={'ok' if pd_ok else 'off'}, "
    f"delivery={'yes' if delivery else 'no'}, "
    f"BPR={'yes' if bpr else 'no'}, "
    f"fib={'ok' if fib_ok else 'low'} ({fib_ext:.2f}x)"
    + (" [no-struct-target penalty]" if target_penalty else "")
)
log.info(reason)
```

Replace the `SetupGrade(...)` constructor call with:
```python
grade = SetupGrade(
    grade=grade_letter,
    score=score_val,
    passes=passes,
    has_delivery_fvg=delivery,
    delivery_fvg_side=delivery_side,
    delivery_fvg_in_pd=delivery_in_pd,
    premium_discount_ok=pd_ok,
    target_clear=target_clear,
    fvg_singular=singular,
    singularity_timeframe=sing_tf,
    momentum_quality=momentum_quality,
    bpr_confluence=bpr,
    bpr_timeframe=bpr_tf,
    recent_sweep_ok=recent_sweep_ok,
    fib_displacement_ok=fib_ok,
    fib_extension=fib_ext,
    ce_respected=ce_respected,
    reason=reason,
)
```

Also remove the now-unused `grade_str` local variable and `_DOWN` penalty dict. The `target_penalty` downgrade now works differently — it still affects `passes` but grade is derived from score. Update the penalty block:

```python
if target_penalty:
    # Downgrade passes: only A/B/C quality setups still trade
    passes = score_val >= 35  # C or better required when no structural target
```

- [ ] **Step 3: Run the full test suite**

```
python -m pytest tests/ -v 2>&1 | tail -30
```

Expected: `test_grader_score.py` all pass. Other tests may fail if they check `grade.grade` — fix those in Task 4.

- [ ] **Step 4: Commit**

```
git add app/strategy/grader.py
git commit -m "feat(grader): wire score into score() and _make_grade() — A-F grading live"
```

---

## Task 4: Fix downstream tests

**Files:**
- Modify: `tests/test_backtest_runner.py`

- [ ] **Step 1: Update the hardcoded `"A+"` fixture**

In `tests/test_backtest_runner.py`, find `test_reconstruct_trades_includes_grade` (~line 151). Change the grade value in the fixture and assertion:

```python
# Before:
"grade": "A+",
# ...
assert trades[0]["grade"] == "A+"

# After:
"grade": "A",
# ...
assert trades[0]["grade"] == "A"
```

The test verifies grade passthrough, not grader logic — any valid string works.

- [ ] **Step 2: Run the full test suite**

```
python -m pytest tests/ -v 2>&1 | tail -30
```

Expected: all tests PASS (or only the pre-existing 6 known failures from `project_known_failing_tests` memory).

- [ ] **Step 3: Commit**

```
git add tests/test_backtest_runner.py
git commit -m "test: update grade fixture from A+ to A for new grading system"
```

---

## Task 5: Add `score` to CSV ledger

**Files:**
- Modify: `app/journaling.py`

- [ ] **Step 1: Add `score` to `_TRADES_HEADERS`**

In `app/journaling.py`, find `_TRADES_HEADERS` (~line 147). Add `"score"` after `"grade_reason"`:

```python
_TRADES_HEADERS = [
    "ts", "instrument", "side", "type", "fill_price", "size", "realized_pnl",
    "broker_order_id",
    "signal_entry", "stop", "target",
    "killzone", "sweep_pattern", "sweep_extreme", "fvg_low", "fvg_high",
    "rationale",
    "contracts", "entry_mode", "r_multiple", "stop_buffer",
    "body_atr_multiple", "vp_enabled",
    "grade", "grade_reason", "score", "slippage",
]
```

- [ ] **Step 2: Add `score` to `_on_pre_place` meta dict**

In the `_on_pre_place` function (~line 66), add score alongside grade:

```python
"grade":             signal.setup_grade.grade if signal.setup_grade else "",
"grade_reason":      signal.setup_grade.reason if signal.setup_grade else "",
"score":             str(signal.setup_grade.score) if signal.setup_grade else "",
```

- [ ] **Step 3: Add `score` to the row list in `_append_fill_csv`**

In `_append_fill_csv` (~line 239), add score in the row list between `grade_reason` and `slippage`:

```python
        # Grade + slippage
        meta.get("grade", ""),
        meta.get("grade_reason", ""),
        meta.get("score", ""),
        _entry_slippage(fill, meta),
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/ -v 2>&1 | tail -20
```

Expected: all tests pass (no test directly checks CSV row order).

- [ ] **Step 5: Commit**

```
git add app/journaling.py
git commit -m "feat(journaling): add score column to trades CSV"
```

---

## Task 6: Update frontend grade styles

**Files:**
- Modify: `frontend/src/components/BacktestsPage.tsx`
- Modify: `frontend/src/types.ts`

- [ ] **Step 1: Update `GRADE_TIERS` and `GRADE_STYLES` in `BacktestsPage.tsx`**

Find the constants near line 68 and 611. Replace:

```tsx
// Line ~68
const GRADE_TIERS = ['A', 'B', 'C', 'D', 'F'] as const
type GradeTier = typeof GRADE_TIERS[number]
```

```tsx
// Line ~611
const GRADE_STYLES: Record<string, { color: string; badge: string }> = {
  'A': { color: 'ring-1 ring-accent text-accent bg-accent/10',       badge: 'bg-accent text-bg' },
  'B': { color: 'ring-1 ring-accent/60 text-accent/80 bg-accent/5',  badge: 'bg-accent/70 text-bg' },
  'C': { color: 'ring-1 ring-warn/60 text-warn bg-warn/5',           badge: 'bg-warn text-bg' },
  'D': { color: 'ring-1 ring-danger/50 text-danger/70 bg-danger/5',  badge: 'bg-danger/60 text-bg' },
  'F': { color: 'ring-1 ring-danger/70 text-danger bg-danger/10',    badge: 'bg-danger text-bg' },
}
```

- [ ] **Step 2: Update the grade comment in `types.ts`**

In `frontend/src/types.ts` (~line 143):

```ts
grade?: string                  // "A" | "B" | "C" | "D" | "F"
```

- [ ] **Step 3: Build frontend**

```
cd frontend && npm run build 2>&1 | Select-Object -Last 8
```

Expected: `✓ built in X.XXs` — no TypeScript errors.

- [ ] **Step 4: Commit**

```
git add frontend/src/components/BacktestsPage.tsx frontend/src/types.ts
git commit -m "feat(frontend): update grade styles for A-F system"
```

---

## Task 7: Restart bot and verify end-to-end

- [ ] **Step 1: Restart the bot** using the `run-bot` skill or via the Restart button in the UI.

- [ ] **Step 2: Check log for new grade format**

```powershell
$root = "C:\Users\Lawrence\Documents\topstep-trader-bot\topstep-bot"
Get-Content "$root\logs\$(Get-Date -Format 'yyyy-MM-dd').log" -Tail 50 |
  % { $_ -replace "`0","" } |
  Select-String "grade [A-F]"
```

Expected: lines like `NY AM: grade D (42) — momentum=decent, P/D=off, ...`

- [ ] **Step 3: Confirm `score` column in trades CSV on next fill**

After the next trade entry, run:

```powershell
Get-Content "C:\Users\Lawrence\Documents\topstep-trader-bot\topstep-bot\trades\trades.csv" -Tail 3 |
  % { $_ -replace "`0","" }
```

Expected: header row contains `score` column; entry rows have an integer in that column.

- [ ] **Step 4: Confirm dashboard grade badges show A/B/C/D/F colors**

Open `http://127.0.0.1:5175` and verify that any grade badge in the signal panel or backtests page shows the new color scheme (A=teal, B=teal/dim, C=amber, D=muted red, F=red).
