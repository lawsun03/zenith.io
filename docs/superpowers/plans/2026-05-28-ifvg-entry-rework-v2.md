# iFVG Entry Rework — Revised Implementation Plan (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Supersedes:** `docs/superpowers/plans/2026-05-28-ifvg-entry-rework.md` — that plan has correct phase structure but six silent bugs and missing rules. This plan is the authoritative version.

**Goal:** Rework 1min entries to true iFVG inversions (prior FVG zones flipped by a displacement bar) and add a `SetupGrader` that scores A+/A/A-/B/B- against Dodgy's 5 criteria + 11 additional rules, filtering below A-, with three selectable entry modes, an exit ladder, and full observability per CLAUDE.md Rule 13.

**Architecture:** `DisplacementDetector` tracks active FVGs and detects inversions (close-through, not wick) using inversion-first ordering to prevent silent drops. A `SetupGrader` (new module) scores signals with corrected singularity (BPR = positive, gapping-sack = reject), directional delivery FVG, Fibonacci displacement quality, and HTF singularity resolution. An armed-zone state machine handles three entry modes (ifvg_edge / retrace_ce / close) with explicit invalidation rules. An exit ladder (Phase 8, approval required) adds TP1/TP2/breakeven. Every feature emits `strategy_state` and renders in `StrategyDebug.tsx`.

**Tech Stack:** Python 3.x / Pydantic / FastAPI / asyncio / Decimal arithmetic; React + TypeScript + Tailwind; WebSocket journal pub/sub.

**Spec:** `docs/superpowers/specs/2026-05-28-ifvg-entry-rework-design.md` (read first; this plan overrides where corrections apply)

---

## ⚠️ Ambiguities — Resolve Before Coding

These must be answered before the relevant phase starts. They are not blocking planning; they block implementation.

**A1 — Phase 8 approval (exit ladder):** Phase 8 touches the existing `_pending_brackets` bracket/fill path in `topstepx.py` and `engine.py`. This is a real-money order path. Per CLAUDE.md Rule 3, do NOT code Phase 8 until explicitly approved after reviewing Phase 8's design section below.

**A2 — Fibonacci "manipulation leg" definition (Rule E):** Is the manipulation leg (a) the single sweep bar (fastest, simplest), or (b) the full move from the last confirmed swing to the sweep extreme? Option (a) is more deterministic; option (b) is closer to Dodgy's visual but requires tracking the originating swing. **Defaulting to (a) until clarified.**

**A3 — BPR timeframe for Rule J (5m vs 15m):** Checking BPR at 5m or 15m requires fetching those bars. Currently only 1min and 30min are polled. Adding a 5min poll loop is a new REST call in `_htf_refresh_loop`. Do we add it, or use the existing 30min bars as a proxy? **Plan assumes 5min poll is added in Phase 6; if not approved, fall back to 30min-only BPR.**

**A4 — Breakeven backtest (Rule K):** The existing backtester (`app/backtest.py`) needs to simulate breakeven stop moves to compare BE-on vs BE-off per grade bucket. Confirm whether this is supported before Phase 8.

**A5 — DST handling for session windows (Rule G):** `Bar.ts` is UTC (confirmed). Session windows (09:00–11:00 NY) need NY-local-to-UTC conversion that accounts for EST/EDT transitions. Use `zoneinfo.ZoneInfo("America/New_York")` for conversion — this is already used in `tests/test_strategy.py`.

---

## New `StrategyParams` Config Fields

All new fields go into `StrategyParams` in `app/bot_config.py`. Each phase notes which fields it adds. Full list:

| Field | Type | Default | Phase |
|-------|------|---------|-------|
| `ifvg_entry_mode` | `str` | `"ifvg_edge"` | 3 |
| `ifvg_stop_buffer_ticks` | `Decimal` | `Decimal("1.0")` | 3 |
| `ifvg_sweep_window_bars` | `int` | `10` | 5 |
| `ifvg_min_displacement_mult` | `Decimal` | `Decimal("1.0")` | 5 |
| `ifvg_session_windows` | `list[str]` | `["09:00-11:00", "02:00-05:00"]` | 6 |
| `ifvg_macro_windows` | `list[str]` | `["08:30-09:10", "09:50-10:10", "10:50-11:10", "13:10-13:40", "15:15-15:45"]` | 6 |
| `ifvg_news_blackout` | `list[str]` | `[]` | 6 |
| `ifvg_tp1_fraction` | `Decimal` | `Decimal("0.5")` | 8 |
| `ifvg_be_after_tp1` | `bool` | `True` | 8 |

Each new field must be: persisted in `save_bot_config`, returned in `GET /api/config`, hot-applied in `PATCH /api/config`, added to `frontend/src/types.ts` and `ConfigPanel`.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `app/strategy/displacement.py` | FVG tracking; inversion-FIRST ordering; wick mitigation; `active_fvgs` property; collision test |
| Modify | `app/strategy/displacement.py` | `peek_displacement` — returns None if no prior FVG to invert |
| Create | `app/strategy/armed_zone.py` | `ArmedZone` dataclass + `ArmedZoneTracker` state machine (entry modes, invalidation) |
| Modify | `app/strategy/composer.py` | `Signal`: add `setup_grade`, `armed_zone`, `ce` fields |
| Modify | `app/strategy/htf.py` | `HTFLevelFinder`: add public `swing_highs` / `swing_lows` properties |
| Create | `app/strategy/grader.py` | `SetupGrade` + `SetupGrader` — all 5 criteria + 6 corrections + rules C/E/I/J |
| Modify | `app/strategy/killzone.py` | Session/macro window helpers (Rule G) |
| Modify | `app/execution/engine.py` | `StrategyRunner`: mandatory grader + armed-zone tracker; `on_bar` grade + arm flow |
| Modify | `app/bot_config.py` | New `StrategyParams` fields (table above) |
| Modify | `app/main.py` | `_build_runner` (grader + armed tracker); `_refresh_htf_once` (grader feeds); 5min poll loop (Rule J) |
| Modify | `app/api/server.py` | `PATCH /api/config` hot-apply for new fields |
| Modify | `app/api/journal.py` | `publish_strategy_state` |
| Create | `frontend/src/components/StrategyDebug.tsx` | Grade badge + all criterion rows + armed zone display |
| Modify | `frontend/src/App.tsx` | Mount `StrategyDebug` |
| Modify | `frontend/src/types.ts` | New config fields + strategy_state shape |
| Modify | `frontend/src/components/ConfigPanel.tsx` | New fields |
| Create | `tests/test_ifvg_inversion.py` | All DisplacementDetector tests incl. collision test |
| Create | `tests/test_armed_zone.py` | Armed zone / entry mode / invalidation tests |
| Create | `tests/test_grader.py` | All grade branches + corrections + rules |
| Create | `tests/test_session_filter.py` | Session/macro/news window tests |

---

## Phase 1: DisplacementDetector — FVG Tracking + Inversion-First Ordering

**Spec correction applied:** Correction 1 (inversion-first, not mitigation-first).

**Core rule:** Inversion = body CLOSE through far edge (kept). Mitigation = WICK through far edge (kept). If both happen on the same bar/FVG, inversion wins: the FVG is inverted, NOT mitigated, and the iFVG signal fires.

### Files changed

**`app/strategy/displacement.py`**

1. Add `_active_fvgs: deque[FairValueGap]` (maxlen=30) to `__init__`.
2. Add `active_fvgs: list[FairValueGap]` public property.
3. Add `_form_fvg(b1, b3) -> FairValueGap | None` — checks b3.low > b1.high (bullish) or b3.high < b1.low (bearish). Called each 3-bar window regardless of displacement.
4. Add `_find_inverted_fvg(displacement_bar, side) -> FairValueGap | None` — searches `_active_fvgs` reversed; bearish disp inverts bullish FVG when `bar.close < fvg.low`; bullish disp inverts bearish FVG when `bar.close > fvg.high`. Returns first (most recent) match.
5. Add `_mitigate_fvgs(bar) -> set[id]` — returns the set of FVG IDs mitigated by WICK; removes from `_active_fvgs` ONLY those NOT inverted. Called AFTER `_find_inverted_fvg`.
6. Rework `on_bar` order: (a) mitigate candidates identified — but hold off on removal; (b) inversion check via `_find_inverted_fvg`; (c) form new FVG from current window; (d) remove mitigated-but-NOT-inverted FVGs from `_active_fvgs`.
7. Rework `_evaluate(b1, b2, b3)` — drops `_compute_fvg`; uses `_find_inverted_fvg(b2, side)` instead. `fvg` in returned `DisplacementEvent` is the prior inverted FVG or None.
8. Update `peek_displacement()` — returns None if `_find_inverted_fvg(b2, side) is None` (no prior FVG to invert).

### Tests (`tests/test_ifvg_inversion.py`)

- `test_bullish_fvg_formed_from_3bar_gap` — b3.low > b1.high → FVG in active_fvgs
- `test_bearish_fvg_formed_from_3bar_gap` — b3.high < b1.low → FVG in active_fvgs
- `test_no_gap_no_fvg` — overlapping bars → active_fvgs empty
- `test_bullish_fvg_mitigated_by_wick` — bar.low <= fvg.low → removed
- `test_bearish_fvg_mitigated_by_wick` — bar.high >= fvg.high → removed
- `test_fvg_not_mitigated_before_far_edge` — bar doesn't reach far edge → stays
- `test_bearish_displacement_inverts_bullish_fvg` — close < fvg.low → iFVG signal, fvg = prior bullish FVG
- `test_bullish_displacement_inverts_bearish_fvg` — close > fvg.high → iFVG signal, fvg = prior bearish FVG
- `test_displacement_no_prior_fvg_returns_fvg_none` — no active FVG → fvg=None
- **`test_inversion_wins_over_mitigation_on_same_bar`** ← **NEW collision test (Correction 1)**: bar whose wick pierces far edge (mitigation) AND body closes through (inversion) → `DisplacementEvent.fvg` is not None, FVG is NOT removed from active_fvgs (it became an iFVG — it is consumed by the inversion, not dropped)
- `test_mitigated_fvg_not_used_for_inversion` — FVG removed by wick mitigation cannot later be inverted
- `test_peek_none_when_no_prior_fvg`
- `test_peek_returns_side_when_prior_fvg_exists`

### strategy_state additions (Phase 1)
```json
{ "active_fvgs_count": 3 }
```

### Backtest gate
Run existing strategy backtest over `bars_cache/` after this phase. Confirm signal count changes (expected: fewer signals; all remaining have a prior FVG). No live change until backtest reviewed.

---

## Phase 2: `peek_displacement` Update + Entry-Mode Scaffolding

**Spec correction applied:** Correction 2 (three entry modes, armed zone concept).

### Files changed

**`app/strategy/displacement.py`**
- `peek_displacement()` already updated in Phase 1 to require a prior FVG. No further changes.

**`app/strategy/armed_zone.py`** ← NEW FILE

Introduce `ArmedZone` dataclass and `ArmedZoneTracker`. This is the state machine that lives between "iFVG inversion confirmed" and "entry filled or invalidated."

```python
@dataclass(frozen=True)
class ArmedZone:
    """An iFVG that is armed for entry pending fill or invalidation."""
    side: Literal["long", "short"]
    fvg_low: Decimal           # iFVG zone bottom
    fvg_high: Decimal          # iFVG zone top
    box_boundary: Decimal      # ifvg_edge entry: bull=fvg_high (ceiling), bear=fvg_low (floor)
    ce: Decimal                # consequent encroachment = (fvg_low + fvg_high) / 2
    entry_mode: str            # "ifvg_edge" | "retrace_ce" | "close"
    entry_price: Decimal       # limit price to arm (mode-dependent)
    stop_price: Decimal        # beyond iFVG extreme + buffer
    created_at: datetime
    killzone: str
```

`ArmedZoneTracker` (one per instrument):
- `arm(signal, entry_mode, stop_buffer_ticks) -> ArmedZone` — builds ArmedZone from a Signal
- `on_bar(bar) -> Literal["filled", "invalidated", "pending"] | None`
  - "filled": bar traded through `entry_price` (for limit modes)
  - "invalidated": bar CLOSES back through iFVG far edge in original direction (body-only)
  - "pending": zone still live
- `cancel()` — external cancel (premature liquidity take, Rule F)
- `active: ArmedZone | None` property

### Tests (`tests/test_armed_zone.py`)

- `test_ifvg_edge_entry_price_is_box_boundary_bull` — long zone: entry = fvg_high
- `test_ifvg_edge_entry_price_is_box_boundary_bear` — short zone: entry = fvg_low
- `test_retrace_ce_entry_price_is_midpoint` — entry = (low + high) / 2
- `test_close_mode_no_wait` — "close" mode arms and immediately signals fill
- `test_wick_back_through_does_not_invalidate` — bar.low < fvg.low but bar.close > fvg.low → still pending
- `test_body_close_back_through_invalidates` — bar.close < fvg.low (bull zone) → invalidated
- `test_arm_entry_after_a_minus_grade_requires_full_body_close` — A- grade: arm only after body clears FVG

### strategy_state additions (Phase 2)
```json
{
  "armed_zone": {
    "active": true,
    "side": "short",
    "fvg_low": "2401.0",
    "fvg_high": "2403.0",
    "box_boundary": "2401.0",
    "ce": "2402.0",
    "entry_price": "2401.0",
    "entry_mode": "ifvg_edge"
  }
}
```

---

## Phase 3: Entry Modes + Invalidation + Stop Placement

**Spec corrections applied:** Corrections 2, 3, Rule C (CE for journaling).

### Files changed

**`app/strategy/armed_zone.py`** — full `on_bar` logic implemented:

Entry mode behavior:
- `ifvg_edge`: limit at box_boundary. Arm on inversion close. Fill when bar trades through box_boundary. No retest required. For A-/B grades: only arm after the inversion bar's body has fully closed over/under the FVG (one-bar wait).
- `retrace_ce`: arm a limit at CE. Fill when price returns to CE. Invalidate (Correction 3) if a bar CLOSES back through the iFVG far edge before fill.
- `close`: treat inversion bar close as fill — no pending state, signals immediately.

Stop placement (deterministic, unit-tested):
- Long: `stop = fvg_low - (ifvg_stop_buffer_ticks × tick_size)`
- Short: `stop = fvg_high + (ifvg_stop_buffer_ticks × tick_size)`

CE computation always happens for journaling (Rule C), regardless of entry mode:
```python
ce: Decimal = (fvg_low + fvg_high) / 2
```

**`app/bot_config.py`**
Add to `StrategyParams`:
```python
ifvg_entry_mode: str = "ifvg_edge"      # "ifvg_edge" | "retrace_ce" | "close"
ifvg_stop_buffer_ticks: Decimal = Decimal("1.0")
```

**`app/api/server.py`** — hot-apply `ifvg_entry_mode` and `ifvg_stop_buffer_ticks` in `PATCH /api/config`.

**`frontend/src/types.ts`** — add `ifvg_entry_mode: string`, `ifvg_stop_buffer_ticks: string`.

**`frontend/src/components/ConfigPanel.tsx`** — add fields for both.

### Tests (extend `tests/test_armed_zone.py`)

- `test_stop_long_is_fvg_low_minus_buffer` — `stop = fvg_low - buffer`
- `test_stop_short_is_fvg_high_plus_buffer` — `stop = fvg_high + buffer`
- `test_a_minus_grade_waits_one_bar_before_arming` — body must clear FVG before entry is armed
- `test_ce_always_computed_regardless_of_mode` — `ce == (fvg_low + fvg_high) / 2` for all modes
- `test_wick_back_through_does_not_invalidate_retrace_mode`
- `test_body_close_back_through_cancels_retrace_mode`

### Config checkpoint (Rule 10)
- `ifvg_entry_mode`, `ifvg_stop_buffer_ticks` in `save_bot_config` / `GET /api/config` / `PATCH /api/config` / `types.ts` / `ConfigPanel`.

---

## Phase 4: Signal Dataclass + HTFLevelFinder Public Properties

**Spec section:** §3 of design spec, unchanged.

### Files changed

**`app/strategy/composer.py`** — `Signal` dataclass additions (all optional, frozen, use `dataclasses.replace`):
```python
setup_grade: "SetupGrade | None" = None
armed_zone: "ArmedZone | None" = None    # the ArmedZone created from this signal
ce: Decimal | None = None               # always computed for journaling (Rule C)
```

**`app/strategy/htf.py`** — add to `HTFLevelFinder`:
```python
@property
def swing_highs(self) -> list[Decimal]: return list(self._swing_highs)

@property
def swing_lows(self) -> list[Decimal]: return list(self._swing_lows)
```

### Tests
- `test_signal_setup_grade_defaults_none` — no regression
- `test_signal_armed_zone_defaults_none`
- `test_htf_level_finder_swing_highs_property` — returns same values as `_swing_highs`

---

## Phase 5: SetupGrader — All Corrections + Rules C/E/I/J

**Spec corrections applied:** Corrections 4 (BPR), 5 (HTF singularity), 6 (directional delivery). Rules: A (sweep prerequisite), C (CE-respected), E (Fibonacci displacement), I (gapping sack), J (BPR auto-A+).

### Files changed

**`app/strategy/grader.py`** ← NEW FILE

#### `SetupGrade` dataclass
```python
@dataclass(frozen=True)
class SetupGrade:
    grade: Literal["A+", "A", "A-", "B", "B-"]
    passes: bool
    # Core criteria
    has_delivery_fvg: bool          # directional delivery (Correction 6)
    delivery_fvg_side: str | None   # "bullish" | "bearish" | None
    delivery_fvg_in_pd: bool        # delivery FVG in correct premium/discount
    premium_discount_ok: bool
    target_clear: bool
    fvg_singular: bool
    singularity_timeframe: str      # "1min" | "30min" | "none"
    momentum_quality: Literal["strong", "decent", "weak"]
    # BPR (Correction 4 / Rule J)
    bpr_confluence: bool            # opposite-side FVG overlap = BPR
    bpr_timeframe: str | None       # "1min" | "5min" | "15min" | None
    # Additional quality signals
    recent_sweep_ok: bool           # sweep within ifvg_sweep_window_bars (Rule A)
    fib_displacement_ok: bool       # reversal ≥ ifvg_min_displacement_mult × manipulation leg (Rule E)
    fib_extension: Decimal          # measured multiple (for strategy_state)
    ce_respected: bool              # retrace touched CE before continuing (Rule C)
    # Grade reason
    reason: str
```

#### `SetupGrader` class — key methods

`score(signal, disp, active_fvgs, bars_since_sweep) -> SetupGrade`

Grade algorithm (in order):

1. **Momentum** — `disp.body_to_atr < 1.0` → B-, fail
2. **Target clarity** — "HTF:" in rationale OR target within 3×ATR of HTF swing/session extreme → bool
3. **FVG singularity** (Corrections 4 + 5 + Rule I):
   - Count same-side active FVGs overlapping entry zone
   - If ≥ 2 consecutive same-side FVGs ("gapping sack", Rule I) → check if entire cluster fits inside ONE 30min FVG (Correction 5). If yes → singular="30min". If no → fvg_singular=False, fail.
   - If 1 same-side overlap → check 30min containment (Correction 5). If fits → singular="30min". If not → fvg_singular=False, fail.
   - Opposite-side overlap → BPR: fvg_singular=True, bpr_confluence=True (do NOT fail, Correction 4)
4. **Sweep prerequisite** (Rule A) — `bars_since_sweep > ifvg_sweep_window_bars` AND `has_delivery_fvg=False` → cap at B
5. **Fail checks complete → at least A-**
6. **Fibonacci displacement quality** (Rule E) — `reversal_range ≥ ifvg_min_displacement_mult × manipulation_range` where manipulation_range = sweep_bar body range → `fib_displacement_ok`, `fib_extension`
7. **Directional delivery FVG** (Correction 6):
   - Long: swept LOW overlaps a BULLISH 30min FVG (in discount of session range) within 0.5×ATR
   - Short: swept HIGH overlaps a BEARISH 30min FVG (in premium of session range) within 0.5×ATR
   - Proximity alone (old spec) is NOT sufficient — side + P/D required
8. **Premium/discount** — session mid AND HTF swing mid check (unchanged from spec)
9. **CE-respected** (Rule C) — if entry_mode == "retrace_ce" and price touched CE before continuing: True; else N/A
10. **Grade assignment**:
    ```
    grade = "A-"
    if premium_discount_ok and momentum_quality == "strong":
        grade = "A"
        if has_delivery_fvg:
            grade = "A+"
    # BPR auto-A+ (Rule J)
    if bpr_confluence and premium_discount_ok and recent_sweep_ok:
        grade = "A+"
    ```

`update_delivery_fvgs(bars_30min)` — scans for unmitigated 30min FVGs using `FairValueGap` type (NOT `_Gap`)

`update_htf_swings(highs, lows)` — store 30min swings

`update_session_range(bar, killzone_name)` — expand/reset per killzone

`has_delivery_fvg(signal) -> bool` — Correction 6 directional check

`_check_fvg_singular(signal, active_fvgs) -> tuple[bool, str, bool, str | None]` — returns (singular, timeframe, bpr, bpr_tf)

`_check_gapping_sack(signal, active_fvgs) -> bool` — True if ≥2 consecutive same-side FVGs

`_check_htf_singularity_rescue(cluster_fvgs, delivery_fvgs_30min) -> bool` — True if all cluster FVGs contained in one 30min FVG

#### New `StrategyParams` fields (Phase 5)
```python
ifvg_sweep_window_bars: int = 10
ifvg_min_displacement_mult: Decimal = Decimal("1.0")
```

### Tests (`tests/test_grader.py`)

**Grade boundary tests (from original spec):**
- `test_grade_b_minus_weak_momentum`
- `test_grade_b_no_clear_target`
- `test_grade_b_fvg_not_singular`
- `test_grade_a_minus_wrong_premium_discount`
- `test_grade_a_correct_pd_strong_momentum`
- `test_grade_a_plus_delivery_fvg`

**Correction/rule tests:**
- `test_opposite_side_overlap_is_bpr_not_singular_fail` — Correction 4: BPR → passes=True, bpr_confluence=True
- `test_same_side_overlap_fails_singularity` — Correction 4: same-side → fails
- `test_gapping_sack_2_consecutive_same_side_fails` — Rule I
- `test_htf_singularity_rescue_contained_in_30min_fvg` — Correction 5: stacked 1min FVGs in one 30min FVG → singular="30min"
- `test_htf_singularity_rescue_fails_if_not_contained` — Correction 5 negative case
- `test_delivery_fvg_requires_correct_side_and_pd` — Correction 6: proximity alone insufficient
- `test_delivery_fvg_wrong_side_not_detected` — Correction 6
- `test_delivery_fvg_correct_side_but_wrong_pd_not_detected` — Correction 6
- `test_no_recent_sweep_caps_at_B_without_delivery` — Rule A
- `test_fib_displacement_ok_when_reversal_2x_manipulation` — Rule E
- `test_fib_displacement_fail_when_under_multiplier` — Rule E
- `test_bpr_auto_a_plus_with_correct_pd_and_sweep` — Rule J
- `test_bpr_does_not_auto_a_plus_without_sweep` — Rule J negative case
- `test_ce_respected_true_when_price_touched_ce` — Rule C

### strategy_state additions (Phase 5)
```json
{
  "grade": "A+",
  "passes": true,
  "has_delivery_fvg": true,
  "delivery_fvg_side": "bearish",
  "delivery_fvg_in_pd": true,
  "premium_discount_ok": true,
  "target_clear": true,
  "fvg_singular": true,
  "singularity_timeframe": "30min",
  "momentum_quality": "strong",
  "bpr_confluence": true,
  "bpr_timeframe": "5min",
  "recent_sweep_ok": true,
  "fib_displacement_ok": true,
  "fib_extension": "2.3",
  "ce_respected": false,
  "reason": "NY AM: A+ — delivery bearish 30min FVG in premium, BPR 5min, fib 2.3x"
}
```

### Backtest gate (Phase 5)
Run backtest; report signal count, win-rate, and expectancy by grade bucket (A+/A/A-). If A- grade win-rate < A win-rate, the A- threshold is too loose — flag before enabling live.

---

## Phase 6: StrategyRunner + Session/Killzone Filter + News Blackout

**Rules applied:** G (session/macro windows), H (news blackout).

### Files changed

**`app/strategy/killzone.py`** — add helpers:
- `session_windows_for_date(windows: list[str], date: date) -> list[tuple[datetime, datetime]]` — converts NY-local time ranges ("09:00-11:00") to UTC datetimes for a given date, handling DST via `zoneinfo.ZoneInfo("America/New_York")`
- `in_session_window(ts: datetime, windows: list[str]) -> bool`
- `in_macro_window(ts: datetime, windows: list[str]) -> bool`
- `in_news_blackout(ts: datetime, blackout_windows: list[str]) -> bool` — parses UTC ISO datetime ranges

**`app/bot_config.py`** — add to `StrategyParams`:
```python
ifvg_session_windows: list[str] = ["09:00-11:00", "02:00-05:00"]   # NY local
ifvg_macro_windows: list[str] = ["08:30-09:10", "09:50-10:10", "10:50-11:10", "13:10-13:40", "15:15-15:45"]
ifvg_news_blackout: list[str] = []   # UTC ISO ranges e.g. "2026-06-06T12:30/2026-06-06T13:00"
```

**`app/execution/engine.py`** — `StrategyRunner`:
- Add `grader: SetupGrader` (mandatory, replaces None from Phase 5)
- In `on_bar()`:
  1. Check `in_news_blackout(bar.ts, cfg.ifvg_news_blackout)` → if True, skip signal (log at INFO)
  2. Check `in_session_window(bar.ts, cfg.ifvg_session_windows)` → if False, skip signal
  3. After grade computed: check `in_macro_window` → if True, log macro-window bonus (grade not changed; kept as observability signal)
  4. Call `grader.update_session_range(bar, kz.name if kz else None)`

### Tests (`tests/test_session_filter.py`)

- `test_signal_blocked_outside_session_window`
- `test_signal_passes_inside_session_window`
- `test_news_blackout_blocks_signal`
- `test_news_blackout_utc_range_correctly_parsed`
- `test_dst_transition_session_window_correct_utc` — bar at 09:30 NY maps to 13:30 UTC in summer, 14:30 UTC in winter
- `test_macro_window_flag_logged_not_grade_changed`

### strategy_state additions (Phase 6)
```json
{
  "in_session_window": true,
  "in_macro_window": false,
  "news_blackout": false
}
```

### Config checkpoint (Rule 10)
- `ifvg_session_windows`, `ifvg_macro_windows`, `ifvg_news_blackout` in `save_bot_config` / `GET /api/config` / `PATCH /api/config` (hot-apply) / `types.ts` / `ConfigPanel`.

---

## Phase 7: main.py Wiring

**Spec section §3 unchanged, plus 5min poll loop for BPR (Rule J / A3).**

### Files changed

**`app/main.py`**

`_build_runner(...)`:
- Import `SetupGrader` from `app.strategy.grader`
- Construct `grader = SetupGrader()` and pass as `grader=grader` to `StrategyRunner`

`_refresh_htf_once(...)` additions after `level_finder.rebuild(...)`:
```python
for runner in engine.runners.values():
    runner.grader.update_delivery_fvgs(swing_bars)
    runner.grader.update_htf_swings(
        level_finder.swing_highs,
        level_finder.swing_lows,
    )
```

5min poll loop (if A3 approved):
- Add `_5min_refresh_loop` alongside the existing HTF loop
- Fetches 5min bars for BPR detection (last 2 days, limit=600)
- Calls `runner.grader.update_bpr_fvgs_5min(bars_5min)`
- `SetupGrader.update_bpr_fvgs_5min` computes 5min FVGs same way as 30min

If A3 NOT approved (no 5min poll): `bpr_timeframe` is limited to "30min" only.

### Tests (`tests/test_main.py` extension)
- `test_grader_instantiated_in_build_runner` — mock build_runner; assert runner.grader is not None
- `test_grader_receives_30min_fvgs_after_htf_refresh`

---

## Phase 8: Exit Ladder — TP1 / TP2 / Breakeven

> ⚠️ **APPROVAL REQUIRED BEFORE CODING.** This phase touches `_pending_brackets` (real-money order path). Do not start until explicit approval after reviewing this section.

**Rules applied:** B (exit ladder), F (premature liquidity cancel), K (configurable breakeven).

### Design

Dodgy's exit model:
- **TP1** = nearest internal swing point in trade direction (first partial target)
- **TP2** = major draw on liquidity (runner — HTF swing high/low or HTF FVG)
- **On TP1 fill**: take `ifvg_tp1_fraction` of position (e.g. 0.5 = half); if `ifvg_be_after_tp1=True`, move stop to breakeven
- **Premature liquidity** (Rule F): if the opposing swing liquidity (TP1 target) is reached BEFORE the pending entry fills (retrace_ce / ifvg_edge modes) → cancel the pending entry (objective already met)

### Execution path change (flag for approval)

Currently `BotConfig.partial_profit_r` exists and `topstepx.py` handles partial fills via `_pending_brackets`. This phase extends that mechanism to:
- Use TP1 as the partial target price (not a fixed R-multiple)
- Use TP2 as the full target
- Wire breakeven stop move on TP1 fill

This requires changes to `app/sim/topstepx.py` and `app/execution/engine.py`'s bracket handling. **Exact diff will be presented for approval before coding.**

### New `StrategyParams` fields (Phase 8)
```python
ifvg_tp1_fraction: Decimal = Decimal("0.5")
ifvg_be_after_tp1: bool = True
```

### Tests (after approval)
- `test_stop_moves_to_breakeven_on_tp1_fill`
- `test_stop_does_not_move_if_be_disabled`
- `test_premature_liquidity_cancels_pending_retrace_entry`
- `test_premature_liquidity_does_not_cancel_filled_entry`

### Backtest gate (Rule K)
Before enabling `ifvg_be_after_tp1=True` as default: run backtest with BE-on vs BE-off per grade bucket. Report scratch-trade rate and expectancy per bucket. Only default BE=True if expectancy is better.

---

## Phase 9: Journal `publish_strategy_state`

**Spec section §4 unchanged.**

### Files changed

**`app/api/journal.py`** — add `publish_strategy_state(grade, instrument, armed_zone, session_state, ...)` method. Emits `kind="strategy_state"` via `_publish`. Called from `StrategyRunner.on_bar()` result in the server's bar dispatch (every bar, not just on signals).

**`app/api/server.py`** — in the bar handler, after `runner.on_bar(bar)`:
```python
journal.publish_strategy_state(
    grade=signal.setup_grade if signal else grader.last_grade,
    instrument=runner.instrument,
    armed_zone=runner.armed_tracker.active,
    ...
)
```

Note: `grader.last_grade` is a new `SetupGrader` attribute that caches the most recent score result (including non-signal bars where grader ran). This ensures the dashboard stays live every bar.

Full `strategy_state` payload (union of all phases):
```json
{
  "instrument": "MGC",
  "grade": "A+",
  "passes": true,
  "has_delivery_fvg": true,
  "delivery_fvg_side": "bearish",
  "delivery_fvg_in_pd": true,
  "premium_discount_ok": true,
  "target_clear": true,
  "fvg_singular": true,
  "singularity_timeframe": "30min",
  "momentum_quality": "strong",
  "bpr_confluence": true,
  "bpr_timeframe": "5min",
  "recent_sweep_ok": true,
  "fib_displacement_ok": true,
  "fib_extension": "2.3",
  "ce_respected": false,
  "reason": "NY AM: A+ — BPR 5min, delivery bearish 30min FVG in premium, fib 2.3x",
  "active_fvgs_count": 3,
  "session_high": "2415.0",
  "session_low": "2390.0",
  "in_session_window": true,
  "in_macro_window": false,
  "news_blackout": false,
  "armed_zone": {
    "active": false
  }
}
```

Also add `setup_grade` to the signal journal payload (the existing signal entry) so post-session review records the grade.

---

## Phase 10: StrategyDebug Frontend

**Spec section §4 unchanged, extended with all new fields.**

### Files changed

**`frontend/src/components/StrategyDebug.tsx`** ← NEW FILE

Collapsible panel (collapsed by default) below the signal panel. Subscribes to `strategy_state` WebSocket events. Renders:

**Grade section:**
- Grade badge (A+/A/A- green, B/B- red) + pass/fail indicator
- Reason text (one line, dimmed)

**Criterion table (5 rows):**
- Sweep present (always ✓)
- iFVG inversion found (fvg_singular, singularity_timeframe)
- Target clear
- Premium/discount
- Delivery FVG (side + P/D flag)

**Quality section:**
- Momentum: strong / decent / weak
- Fib extension: `2.3×` (green if fib_displacement_ok)
- BPR: timeframe label if bpr_confluence=true
- CE respected: yes/no/N-A

**Armed zone section (if armed_zone.active):**
- Entry mode badge
- iFVG zone: `[fvg_low — fvg_high]`
- Box boundary: `2401.0`
- CE line: `2402.0`
- Status: armed / invalidated

**Session section:**
- Session high / low
- Window: in-session (green) / outside (dim)
- Macro: active (amber) / inactive (dim)
- News blackout: active (red) / clear (dim)

**`frontend/src/App.tsx`** — import and mount `<StrategyDebug wsUrl={wsUrl} />` after the signal panel.

**`frontend/src/types.ts`** — add `StrategyStatePayload` type with all fields.

---

## Phase 11: Full Test Suite

Ensure every grade branch, correction, and rule has a test that can FAIL when the business logic regresses (not just type-checks).

### Tests to confirm present and failing-on-regression

From Phase 1: all inversion/mitigation/collision tests
From Phase 2: all armed zone / invalidation tests
From Phase 3: stop placement determinism, CE computation
From Phase 5 grader: all 15+ named tests above
From Phase 6: all session/macro/news tests

**Additional integration tests (`tests/test_integration.py` extension):**
- `test_e2e_a_plus_signal_fires_and_grades` — full bar sequence: form FVG → sweep → displace through FVG → grade A+ → signal emitted
- `test_e2e_b_minus_blocked_by_grader` — low momentum → grader blocks signal before composer emits
- `test_e2e_outside_session_window_blocked` — bar in session gap → no signal
- `test_e2e_news_blackout_blocked`

Run full suite after each phase. Any phase that breaks existing tests is not shippable.

---

## Cross-Cutting Rules (All Phases)

- **TDD**: write the failing test first, then implement. Each phase's test block runs GREEN before that phase is marked done.
- **Body-close everywhere**: audit every `bar.high`, `bar.low`, `bar.close` comparison — inversion and invalidation use `.close`; only mitigation uses wick extremes.
- **Decimal arithmetic throughout**: no floats in price math. All new Decimal fields use `Decimal(...)` literals, not `float(...)`.
- **Backtest after each phase**: run `app/backtest.py` over `bars_cache/`; report signal count and expectancy. No live change until reviewed.
- **Config checkpoint per phase**: every new `StrategyParams` field follows the full plumbing: `save_bot_config` → `GET /api/config` → `PATCH /api/config` (hot-apply) → `types.ts` → `ConfigPanel`.
- **Surgical**: do not refactor `liquidity.py`, `pretrade.py`, or the bracket/fill path beyond what each phase explicitly requires. Match existing snake_case / type-hint / logging conventions.
