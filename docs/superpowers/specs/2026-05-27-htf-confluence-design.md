# HTF Confluence — 4h Bias Filter + HTF Target Selection

**Date:** 2026-05-27
**Status:** Approved design, pending implementation plan
**Scope:** Add higher-timeframe (HTF) confluence to the existing sweep+displacement
strategy. Two coupled features, both opt-in and defaulting off:

- **Part A — 4h Bias Filter:** the 4-hour swing structure establishes a
  directional bias; signals that fight the bias are blocked.
- **Part B — HTF Target Selection:** replace the fixed R-multiple target with a
  real HTF destination — nearest unmitigated 4h FVG, else nearest 30min swing.

---

## Motivation

The strategy is purely a reversal pattern (liquidity sweep → opposite-direction
displacement → FVG entry). It has no concept of the broader trend. Two observed
failure modes motivate this work:

1. **No trend awareness.** During sustained directional moves the bot either
   stands aside or takes counter-trend reversals that get run over.
2. **VP filter blocks structurally-correct trades.** On 2026-05-27 the prior
   session value area sat ~50 points above price all day. Every short signal was
   rejected by the VP value-area filter (`SIGNAL DENIED ... reason=vp_filter`),
   even though the market was clearly trending down — exactly when shorts should
   fire.

A 4h bias addresses both: it confirms the direction the market actually favors,
and (per the decision below) lets bias-aligned signals bypass the VP value-area
filter that would otherwise reject them.

---

## Key constraint discovered during design

The live bar pipeline is **single-timeframe by construction**. The broker's
`_on_new_bar` handler (`app/broker/topstepx.py`) hardcodes `tf_list[0]` — it only
fetches and fans out bars for the *first* subscribed timeframe, and the `Bar`
events it emits carry no usable timeframe routing. Adding `"4h"`/`"30min"` to the
`subscribe()` call would **not** deliver those bars to the strategy, and
reworking NEW_BAR routing means surgery on the real-money order path (avoid per
CLAUDE.md Rule 8).

**Therefore HTF bars are sourced via REST polling**, mirroring the existing VP
warm-up (`_warm_up_vp`, `app/main.py`): an initial synchronous fetch at startup
so bias is ready before the first live signal, then a lightweight background task
that refreshes 4h and 30min bars on a cadence. 4h structure barely moves
intraday, so a 60s refresh is more than sufficient and costs two REST calls.

**Timeframe-string trap:** `_parse_timeframe` treats a trailing `h` as hours and
`min` as minutes. `"4hr"` ends in `r` and silently falls through to the 1min
default. The correct strings are **`"4h"`** and **`"30min"`**.

---

## Architecture

### New module: `app/strategy/htf.py` (pure, no I/O)

Two small classes, each bars-in / state-out, independently testable:

**`HTFBiasTracker`**
- Consumes a list of completed 4h bars (rebuilt on each refresh).
- Tracks swing highs/lows using the existing swing convention
  (`swing_lookback`, default 3 for HTF).
- Computes `bias() -> Literal["bullish", "bearish", "neutral"]`:
  - `bullish` — most recent confirmed structure is higher-high **and** higher-low.
  - `bearish` — lower-high **and** lower-low.
  - `neutral` — mixed structure, or fewer than 2 confirmed swings of each kind
    (insufficient data → fail-open, consistent with VP's "filter inactive when
    data unavailable" convention).

**`HTFLevelFinder`**
- Consumes completed 4h bars (for FVGs) and 30min bars (for swings).
- 4h FVGs: reuse `DisplacementDetector._compute_fvg` logic on 4h 3-bar windows;
  track unmitigated FVGs (price has not yet traded back through the gap).
- 30min swings: track the last N swing highs and lows.
- `find_target(side, entry, stop, min_r) -> tuple[Decimal, str] | None`:
  returns `(price, label)` for the nearest level in the signal direction that
  delivers at least `min_r` of R, preferring a 4h FVG over a 30min swing. Returns
  `None` if nothing qualifies (caller falls back).

These are fed by REST data; they never call the broker themselves.

### Wiring: `app/main.py`

- **Startup warm-up** (`_warm_up_htf`, modeled on `_warm_up_vp`): fetch enough
  4h bars (e.g. `days=30`) and 30min bars (e.g. `days=10`) to establish structure,
  feed both trackers, log readiness. On fetch failure, trackers stay empty →
  bias `neutral`, target finder returns `None` → features inert (fail-open).
- **Refresh loop** (`_htf_refresh_loop`): a background asyncio task started in
  `_run_live` alongside the engine/reconciler, cancelled on shutdown. Every
  `HTF_REFRESH_SECONDS` (60), re-fetch recent 4h + 30min bars and rebuild tracker
  state. A failed refresh logs and retains the last-known state (never crashes
  the loop — same discipline as the reconciler).
- Trackers are attached to the `StrategyRunner` so the engine can read them at
  signal-decision time (alongside `runner.vp`).

### Integration: `app/execution/engine.py`

The signal pipeline in `EngineState.on_bar` currently is: run detectors → signal
→ VP filter+target (`runner.vp.apply`) → `_act_on_signal`. The HTF logic slots in
around the VP step. New order when all features enabled:

1. **Signal produced** by the composer/runner (unchanged).
2. **Part A — bias gate:** if `htf_bias_enabled` and bias is decisive
   (`bullish`/`bearish`), deny signals that fight it:
   - `bias == bullish` → block short signals.
   - `bias == bearish` → block long signals.
   - `bias == neutral` → no blocking.
   Denials surface as `OrderOutcome(placed=False, reason="htf_bias")` and log a
   one-line reason (same shape as the existing `vp_filter` denial).
3. **VP filter — with bias bypass:** the VP value-area filter still runs, **except**
   when `htf_bias_enabled` and the 4h bias *agrees* with the signal direction —
   in that case the value-area filter is skipped for this signal (a bias-aligned
   trade is allowed through even if price is outside the prior value area). VP
   target selection is unaffected by this bypass.
4. **Part B — target selection precedence** (when `htf_target_enabled`):
   1. `HTFLevelFinder.find_target(...)` — nearest 4h FVG, else nearest 30min
      swing, clearing `htf_target_min_r`.
   2. else VP target (existing `_pick_target`, when VP enabled + qualifies).
   3. else fixed `r_multiple` (existing default).
   The chosen target is written via `dataclasses.replace(signal, target=...,
   rationale=...)`, appending a label like `| HTF: 4h FVG @ 4531.4 (3.2R)`.
5. `_act_on_signal(signal)` (unchanged).

Implementation note: to keep precedence clean, target selection is consolidated
so VP's `apply()` is not the sole target authority when HTF is on. The bias
bypass is passed into the VP step as a flag; VP's filter check is gated on it,
its target logic stays intact for the fallback case.

### Config: `app/bot_config.py` (`StrategyParams`)

New fields, all defaulting to inert values (no behavior change until enabled):

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `htf_bias_enabled` | bool | `False` | Master switch for Part A. |
| `htf_bias_timeframe` | str | `"4h"` | HTF used for bias. |
| `htf_bias_lookback` | int | `3` | Swing lookback on the bias timeframe. |
| `htf_target_enabled` | bool | `False` | Master switch for Part B. |
| `htf_target_min_r` | Decimal | `2.0` | Min R an HTF level must deliver to be used as target. |
| `htf_swing_timeframe` | str | `"30min"` | Timeframe for fallback swing targets. |

Per CLAUDE.md Rule 10, both switches require the full config lifecycle: persisted
in `save_bot_config`, returned by `GET /api/config`, hot-applied in
`PATCH /api/config`, added to frontend `types.ts` + `ConfigPanel`. Hot-apply for
the HTF fields rebuilds/refreshes the trackers the same way a VP reload re-warms
the profile.

---

## Data flow

```
startup ──► _warm_up_htf ──► fetch 4h+30min via REST ──► feed trackers
                                                              │
background ─► _htf_refresh_loop (every 60s) ─► refetch ─► rebuild trackers
                                                              │
1min bar ─► runner.on_bar ─► signal ──┐                       │ (read at
                                      ▼                        ▼  decision time)
                          ┌─ Part A bias gate ──────► deny "htf_bias"
                          │            │ (pass / neutral)
                          ▼            ▼
                          VP filter (bypassed if bias agrees) ─► deny "vp_filter"
                                       │ (pass)
                                       ▼
                          Part B target: 4h FVG ▸ 30min swing ▸ VP ▸ fixed R
                                       │
                                       ▼
                                 _act_on_signal
```

---

## Error handling & fail-open behavior

- **REST fetch fails (startup or refresh):** trackers retain last-known state
  (or stay empty at startup). Empty bias tracker → `neutral` → Part A inert.
  Empty level finder → `find_target` returns `None` → Part B falls back to VP/
  fixed R. The bot keeps trading on 1min logic exactly as today. Logged at
  WARNING; never silent (Rule 12).
- **Refresh loop exception:** caught and logged per-tick; loop continues (mirrors
  reconciler `_run`).
- **Feature disabled:** zero new code paths execute beyond a boolean check.

---

## Testing (`tests/test_htf.py` + additions to `tests/test_engine.py`)

Tests must encode *why* (Rule 9):

**`HTFBiasTracker`**
- HH+HL sequence → `bullish`; LH+LL → `bearish`; mixed → `neutral`;
  insufficient bars → `neutral` (fail-open).

**`HTFLevelFinder`**
- A constructed 4h bullish FVG above entry that clears min-R is returned as the
  target with the correct price and label.
- When the 4h FVG is too close (below min-R), it falls through to a qualifying
  30min swing.
- When nothing qualifies, returns `None`.
- A mitigated (already-traded-through) 4h FVG is not returned.

**Engine integration**
- `htf_bias_enabled`, bias `bullish`: a short signal is denied with
  `reason="htf_bias"` and the broker is never asked to place.
- bias `bearish`: a long signal denied; a short allowed.
- bias `neutral`: neither direction blocked.
- **Bias bypass:** bias `bearish`, short signal whose entry is outside the prior
  value area (would be `vp_filter`-denied today) is *allowed through* and reaches
  `_act_on_signal`. This is the regression test for the 2026-05-27 "all shorts
  rejected" case.
- **Target precedence:** with `htf_target_enabled` and a qualifying 4h FVG, the
  placed signal's target equals the FVG price, not the VP HVN.
- Both features disabled → behavior byte-for-byte identical to current (a guard
  test asserting no target/denial changes when flags are off).

---

## Out of scope (YAGNI)

- Reworking the NEW_BAR pipeline for true multi-timeframe streaming.
- Full top-down setup confluence (4h displacement → 15min sweep → 1min entry) —
  this was option C in brainstorming and was not selected.
- 15min timeframe — only 4h (bias + FVG targets) and 30min (swing targets) are
  in scope.
- Replacing or retuning the VP filter itself beyond the bias-bypass.
- The reverted `trend_ema_period` — superseded by 4h structure, left as-is (0).
