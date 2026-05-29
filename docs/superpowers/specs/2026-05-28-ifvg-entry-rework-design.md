# iFVG Entry Rework — Dodgy's Setup Rating System

**Date:** 2026-05-28
**Branch:** feat/risk-based-sizing (or new branch)
**Status:** Approved for planning

---

## Motivation

The current bot enters at the new 3-bar FVG created by the displacement bar after a sweep. Dodgy's iFVG setup rating system requires entries at **inverted** Fair Value Gaps — prior FVGs that a displacement bar has closed through, flipping the zone's bias. These are structurally different entry zones and the prerequisite for Dodgy's A+/A/A-/B/B- grading system.

This spec covers:
1. Reworking `DisplacementDetector` to track prior FVGs and detect inversions (true iFVG entry)
2. A new `SetupGrader` that scores each signal against Dodgy's 5 criteria and filters below A-

---

## Glossary

| Term | Definition |
|------|-----------|
| FVG | Standard 3-bar imbalance: bullish (b3.low > b1.high) or bearish (b3.high < b1.low) |
| iFVG | An FVG that a subsequent bar has closed through (inverted). The zone flips bias: bearish FVG inverted → bullish iFVG; bullish FVG inverted → bearish iFVG |
| Inversion | A bar whose close crosses the far edge of an active FVG, converting it to an iFVG |
| Far edge | The boundary a bar must close through to invert: bullish FVG far edge = fvg.low; bearish FVG far edge = fvg.high |
| Mitigation | Price trading through the far edge of a FVG without an iFVG inversion (removes it from tracking) |
| Delivery from FVG | The swept swing originated at or near a 30min FVG before the inversion — the A+ confluence |
| Session range | Developing high/low since the current killzone opened |
| Premium | Price above the 50% midpoint of a range (shorts preferred) |
| Discount | Price below the 50% midpoint of a range (longs preferred) |

---

## Architecture

```
Bar stream (1min, closed)
  ├─ LiquidityTracker
  │     └─ SweepEvents → composer.on_sweep()
  │
  ├─ DisplacementDetector (reworked)
  │     ├─ _active_fvgs: deque[FairValueGap]  ← all unmitigated 1min FVGs
  │     │     • formed: every 3-bar window that creates a gap
  │     │     • mitigated: price trades back through the far edge
  │     ├─ on every bar: form new FVGs, mitigate stale ones
  │     └─ _evaluate(): if bar2 body closes through a prior active FVG
  │                      → DisplacementEvent with fvg = that iFVG zone
  │                      (no prior FVG inverted → fvg = None → no signal)
  │
  └─ StrategyRunner.on_bar()
        sweeps + disp → composer → Signal candidate
        if signal:
          grade = grader.score(signal, disp, runner.displacement.active_fvgs)
          grade < A-  → log reason, discard
          grade >= A- → attach grade to Signal, return

HTF refresh loop (every 60s, unchanged cadence)
  ├─ 4h bars  → HTFBiasTracker.rebuild()
  ├─ 30min bars → HTFLevelFinder.rebuild()
  └─ 30min bars → grader.update_delivery_fvgs(swing_bars_30min)
               └─ level_finder._swing_highs/lows → grader.update_htf_swings()
```

---

## 1. DisplacementDetector Rework

### FVG formation (new)

Every time the 3-bar window advances (a new b3 closes), form and record any new FVG:

```python
def _form_fvg(self, b1: Bar, b2: Bar, b3: Bar) -> FairValueGap | None:
    if b3.low > b1.high:
        return FairValueGap(side="bullish", low=b1.high, high=b3.low, created_at=b3.ts)
    if b3.high < b1.low:
        return FairValueGap(side="bearish", low=b3.high, high=b1.low, created_at=b3.ts)
    return None
```

New internal state:
```python
self._active_fvgs: Deque[FairValueGap] = deque(maxlen=30)
```

### Mitigation (new)

Before forming a new FVG, check whether the current bar has mitigated any active FVGs:

- Bullish FVG mitigated: `bar.low <= fvg.low` (price traded back to the far edge)
- Bearish FVG mitigated: `bar.high >= fvg.high`

Remove mitigated FVGs from `_active_fvgs`.

### iFVG inversion detection (replaces current `_compute_fvg`)

`_evaluate(b1, b2, b3)` searches `_active_fvgs` for a FVG that bar2's body closed through:

```
For each fvg in _active_fvgs (most-recent first):
    bearish displacement (b2.close < b2.open):
        if fvg.side == "bullish" and b2.close < fvg.low:
            → DisplacementEvent(side="bearish", fvg=fvg)  # bearish iFVG
    bullish displacement (b2.close > b2.open):
        if fvg.side == "bearish" and b2.close > fvg.high:
            → DisplacementEvent(side="bullish", fvg=fvg)  # bullish iFVG

If no prior FVG matched → fvg = None (displacement exists, no iFVG entry)
```

The displacement body/ATR thresholds (body_atr_multiple, min_body_to_range_ratio, min_absolute_body) remain unchanged — they still gate what counts as a displacement.

### Public property (new)

```python
@property
def active_fvgs(self) -> list[FairValueGap]:
    return list(self._active_fvgs)
```

Used by the grader for singularity checks.

### peek_displacement (updated)

`try_signal_from_forming` in the engine uses `peek_displacement()`. Update it to also search `_active_fvgs` for an inversion on the forming bar, same logic as `_evaluate`. If no active FVG is inverted by the forming bar → return None (no iFVG yet).

---

## 2. SetupGrader — new file `app/strategy/grader.py`

### Public types

```python
@dataclass(frozen=True)
class SetupGrade:
    grade: Literal["A+", "A", "A-", "B", "B-"]
    passes: bool                           # True if grade >= A-
    has_delivery_fvg: bool                 # swept swing at a 30min FVG
    premium_discount_ok: bool              # entry in correct half of both ranges
    target_clear: bool                     # target aligns with a structural level
    fvg_singular: bool                     # no overlapping active 1min FVGs
    momentum_quality: Literal["strong", "decent", "weak"]
    reason: str                            # one-line log/dashboard message


class SetupGrader:
    # Updated by HTF refresh loop
    def update_delivery_fvgs(self, bars_30min: list[Bar]) -> None
    def update_htf_swings(self, highs: list[Decimal], lows: list[Decimal]) -> None

    # Updated every bar by StrategyRunner
    def update_session_range(self, bar: Bar, killzone_name: str | None) -> None

    # Called once per signal candidate
    def score(
        self,
        signal: Signal,
        disp: DisplacementEvent,
        active_fvgs: list[FairValueGap],
    ) -> SetupGrade
```

### Grade algorithm

```
# body_to_range: computed by grader from disp.displacement_bar directly
#   body = abs(bar.close - bar.open)
#   bar_range = bar.high - bar.low
#   body_to_range = body / bar_range  (0 if bar_range == 0)

# Step 1 — momentum quality (from DisplacementEvent)
if disp.body_to_atr < 1.0:
    momentum_quality = "weak" → grade = "B-", passes = False

# Step 2 — target clarity
#   htf_swing_highs: nearest high ABOVE entry price (long target zone)
#   htf_swing_lows:  nearest low BELOW entry price (short target zone)
target_clear = (
    "HTF:" in signal.rationale                         # HTFLevelFinder found a level
    OR signal.target is within 3×ATR of the nearest htf_swing_high above entry (longs)
    OR signal.target is within 3×ATR of the nearest htf_swing_low below entry (shorts)
    OR signal.target is within 3×ATR of session_high (shorts) / session_low (longs)
)
if not target_clear → grade = "B", passes = False

# Step 3 — FVG singularity
fvg_singular = no FVG in active_fvgs (other than signal's own iFVG)
               overlaps with [signal.fvg_low, signal.fvg_high]
if not fvg_singular → grade = "B", passes = False

# Step 4 — grade assignment (all fail checks passed → at least A-)
momentum_quality = "strong" if disp.body_to_atr >= 1.5 AND body_to_range >= 0.6
                 = "decent" otherwise

premium_discount_ok:
    session_mid = (session_high + session_low) / 2
    htf_mid = (nearest_swing_high_above_entry + nearest_swing_low_below_entry) / 2
    long:  entry < session_mid AND entry < htf_mid
    short: entry > session_mid AND entry > htf_mid

has_delivery_fvg:
    any 30min unmitigated FVG zone overlaps signal.sweep_extreme ± (0.5 × disp.atr_at_event)

grade = "A-"
if premium_discount_ok AND momentum_quality == "strong":
    grade = "A"
    if has_delivery_fvg:
        grade = "A+"
```

### Session range tracking

`SetupGrader` maintains `_session_ranges: dict[str, tuple[Decimal, Decimal]]` (killzone_name → (high, low)). `update_session_range(bar, killzone_name)`:
- If killzone changed from last bar → reset that killzone's range to bar.high / bar.low
- Otherwise → expand: high = max(high, bar.high), low = min(low, bar.low)
- If killzone_name is None → no update (between sessions)

### 30min delivery FVG tracking

`update_delivery_fvgs` runs the same 3-bar gap scan as `HTFLevelFinder._compute_unmitigated_gaps` but uses `FairValueGap` (from `displacement.py`) rather than the private `_Gap` type from `htf.py`. Grader owns this scan inline — no cross-module private type dependency. Stored as `_delivery_fvgs_30min: list[FairValueGap]`.

---

## 3. Changes to Existing Files

### `app/strategy/composer.py` — Signal dataclass

Add one optional field at the end (frozen dataclass, uses `dataclasses.replace` to attach grade):

```python
setup_grade: "SetupGrade | None" = None
```

No logic changes to the composer itself.

### `app/execution/engine.py` — StrategyRunner

Add mandatory field:
```python
grader: SetupGrader   # no default — always required
```

Update `on_bar()`:
```python
def on_bar(self, bar: Bar) -> Optional[Signal]:
    sweeps = self.liquidity.on_bar(bar, atr=self._prev_atr)
    for s in sweeps:
        self.composer.on_sweep(bar, s)

    signal: Optional[Signal] = None
    disp = self.displacement.on_bar(bar)
    if disp is not None:
        candidate = self.composer.on_displacement(bar, disp)
        if candidate is not None:
            grade = self.grader.score(candidate, disp, self.displacement.active_fvgs)
            if grade.passes:
                signal = replace(candidate, setup_grade=grade)
            else:
                log.info("Signal filtered: grade %s — %s", grade.grade, grade.reason)

    self._prev_atr = self.displacement.atr

    # in_killzone imported from app.strategy.killzone (already used by composer)
    kz = in_killzone(bar.ts, self.composer._zones)
    self.grader.update_session_range(bar, kz.name if kz else None)

    self.composer.on_bar_close(bar)
    return signal
```

### `app/main.py` — `_refresh_htf_once` and `_build_runner`

After `level_finder.rebuild(...)`:
```python
if hasattr(runner, 'grader'):
    runner.grader.update_delivery_fvgs(swing_bars)
    runner.grader.update_htf_swings(
        level_finder.swing_highs,   # public property — add to HTFLevelFinder
        level_finder.swing_lows,    # public property — add to HTFLevelFinder
    )
```

**Note:** Add `swing_highs: list[Decimal]` and `swing_lows: list[Decimal]` as public properties to `HTFLevelFinder` (currently private `_swing_highs` / `_swing_lows`).

In `_build_runner` (or wherever `StrategyRunner` is constructed): instantiate `SetupGrader()` and pass it as `grader=`.

---

## 4. Observability (Rule 13)

### Backend — journal

Add to `Journal`:
```python
def publish_strategy_state(self, grade: SetupGrade | None, instrument: str) -> None
```
Emits `kind="strategy_state"` WebSocket event each bar. Called from `StrategyRunner.on_bar()` after grading (whether or not a signal fired).

Payload shape:
```json
{
  "type": "strategy_state",
  "instrument": "MGC",
  "grade": "A-",
  "passes": true,
  "has_delivery_fvg": false,
  "premium_discount_ok": true,
  "target_clear": true,
  "fvg_singular": true,
  "momentum_quality": "decent",
  "reason": "NY AM: decent momentum, correct P/D, no 30min delivery FVG",
  "active_fvgs_count": 3,
  "session_high": "2345.6",
  "session_low": "2338.2"
}
```

Also add `setup_grade` to the signal journal payload so post-session review shows the grade.

### Frontend — `StrategyDebug.tsx`

New collapsible component, collapsed by default, below the signal panel in `App.tsx`. Subscribes to `strategy_state` WebSocket events and renders:

- **Grade badge**: A+/A/A- in green, B/B- in red with reason text
- **5 criterion rows**: each showing a checkmark or cross with label
  - Sweep present (always ✓)
  - iFVG inversion found
  - Target clear
  - FVG singular
  - Premium/discount correct
- **Momentum**: strong / decent / weak
- **Session range**: developing high / low

---

## 5. Not In Scope

- **Equal highs/lows (EQH/EQL) detection**: target clarity uses HTF swings and session extremes as proxies; true equal-high detection deferred
- **LRLR trendline liquidity**: deferred
- **Data highs/lows (news levels)**: deferred
- **Grade-based sizing**: pass/fail only; size unchanged across grades
- **B+ grade distinction**: treated as A- (B+ = A- on bad days; impossible to detect algorithmically)

---

## 6. Test Plan

| Test | What it verifies |
|------|-----------------|
| FVG formation | `_active_fvgs` grows when 3-bar gaps form |
| Mitigation | FVG removed when bar trades through far edge |
| iFVG inversion | `DisplacementEvent.fvg` is the prior FVG, not displacement bar's own gap |
| No prior FVG | `DisplacementEvent.fvg = None` when no active FVG to invert |
| Grade B- | Signal with weak momentum (body_to_atr < 1.0) is filtered |
| Grade B (target) | Signal with no HTF level and no structural level near target is filtered |
| Grade B (singular) | Signal where entry zone overlaps another active FVG is filtered |
| Grade A- | Signal passes with correct P/D lacking or chop present |
| Grade A | Signal with strong momentum + correct P/D grades A |
| Grade A+ | Grade A + delivery FVG match grades A+ |
| Session range | Resets on killzone change, expands within session |
| Delivery FVG | Swept swing within ATR tolerance of a 30min FVG → has_delivery_fvg = True |

---

## 7. Implementation Order

1. Rework `DisplacementDetector`: FVG tracking, mitigation, inversion detection, `active_fvgs` property
2. Update `peek_displacement()` for iFVG logic
3. Add `setup_grade` field to `Signal`
4. Create `app/strategy/grader.py` with `SetupGrade` + `SetupGrader`
5. Add `grader` as mandatory field to `StrategyRunner`; update `on_bar()` with grade call + session range update
6. Wire grader into `_build_runner` and `_refresh_htf_once` in `main.py`
7. Add `publish_strategy_state` to `Journal`; call from bar handler
8. Create `frontend/src/components/StrategyDebug.tsx`
9. Wire `StrategyDebug` into `App.tsx`
10. Write tests for all grade branches and iFVG inversion logic
