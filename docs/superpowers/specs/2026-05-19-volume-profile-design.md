# Volume Profile Filter + Target — Design Spec

**Date:** 2026-05-19
**Status:** Approved

---

## Overview

Add a prior-session volume profile layer to the existing sweep+displacement strategy.
Volume profile serves two roles:

1. **Filter** — only accept signals whose FVG entry price is within or near the prior session's value area
2. **Target** — replace the fixed R-multiple target with the nearest significant VP level (POC, VAH/VAL, HVN) that delivers at least 1:1 R

Reference: https://www.tradingsim.com/blog/advanced-day-trading-strategies-using-volume-profile

---

## Architecture

### New module

`app/strategy/volume_profile.py` — pure module, no broker or I/O dependency.

### Existing modules touched

- `app/execution/engine.py` — wire VP tracker into `StrategyRunner`; feed historical bars at startup; apply VP gate after signal emission
- `app/bot_config.py` — add six new fields to `StrategyParams`

### Unchanged

`composer.py`, `liquidity.py`, `displacement.py`, `pretrade.py` — all untouched.

### Data flow

```
Bar stream
  ├── LiquidityTracker      → SweepEvents
  ├── DisplacementDetector  → DisplacementEvents
  ├── VolumeProfileTracker  → prior session profile (maintained as side-effect)
  └── SweepDisplacementComposer → Signal
                                    ↓
                              VP gate (engine):
                                filter fails? → drop signal (log reason)
                                filter passes? → dataclasses.replace(signal, target=vp_target, rationale=...)
                                    ↓
                              pretrade risk check
                                    ↓
                              broker.place(...)
```

---

## VolumeProfileTracker

### Profile construction

Each bar's volume is distributed uniformly across its high-low range, quantized to `vp_tick_size`.

```
bar: high=1902.0, low=1899.0, volume=120, tick_size=0.10
→ 30 bins, 4 volume units each
→ {1899.0: 4, 1899.1: 4, ..., 1902.0: 4}
```

Bins are accumulated per session in a `dict[Decimal, int]`.

### Session boundary

On each `on_bar()` call, compare the bar's UTC date to the current session date.
If the date changed:
- Finalize the current accumulator → compute and store `prior_profile`
- Reset the accumulator
- Begin new session with the incoming bar

First day (no prior profile yet): `has_prior_profile()` returns `False`. VP gate passes all signals through — no filter, no target override.

### Profile outputs (computed once at session close)

| Field | Description |
|-------|-------------|
| `poc` | Price level with highest volume |
| `vah` | Upper bound of the 70% value area (configurable %) |
| `val` | Lower bound of the 70% value area |
| `hvns` | List of price levels where volume > mean × `vp_hvn_threshold` |

**Value area algorithm:**
1. Start with the POC bin; cumulative volume = POC volume
2. Expand outward one bin at a time (always adding the higher-volume neighbor)
3. Stop when cumulative volume ≥ total × `vp_value_area_pct`
4. `vah` = highest price in the expanded set; `val` = lowest

### Public interface

```python
class VolumeProfileTracker:
    def on_bar(self, bar: Bar) -> None: ...
    def has_prior_profile(self) -> bool: ...
    def apply(self, signal: Signal, cfg: StrategyParams) -> Signal | None:
        # Returns None if filtered out, otherwise Signal with VP target + updated rationale
```

---

## Filter Logic (Loose)

A signal is accepted if the FVG entry price satisfies **any one** of:

- Entry is between `val` and `vah` (inside value area), OR
- Entry is within `vp_filter_tolerance` price units of `vah` or `val`

**Rejection:** Entry is further than `vp_filter_tolerance` outside the value area on the wrong side.

**Directional edge cases:**

| Signal side | Entry location | Decision |
|-------------|---------------|----------|
| Long | Below VAL (discount zone) | **Pass** |
| Long | Inside VA | **Pass** |
| Long | Above VAH + tolerance | **Reject** (buying into resistance) |
| Short | Above VAH (premium zone) | **Pass** |
| Short | Inside VA | **Pass** |
| Short | Below VAL − tolerance | **Reject** (selling into support) |

**No prior profile:** pass through unconditionally.

Default `vp_filter_tolerance`: `2.0` price units (~20 ticks on /MGC).

---

## Target Selection

Replaces `entry ± r * r_multiple` with the nearest VP level in the trade direction that clears the minimum R gate.

### Candidate levels

Gathered and sorted by proximity from entry:

- POC (if on the correct side of entry)
- VAH (longs) / VAL (shorts)
- All HVNs on the correct side of entry

### Selection

```
LONG:
  candidates = [level for level in [poc, vah] + hvns if level > entry], sorted ascending
  pick first candidate where (candidate - entry) >= (entry - stop) * vp_min_target_r

SHORT:
  candidates = [level for level in [poc, val] + hvns if level < entry], sorted descending
  pick first candidate where (entry - candidate) >= (stop - entry) * vp_min_target_r
```

### Fallback

If no candidate clears the minimum R gate, use the existing R-multiple target unchanged.

### Rationale update

On VP target: append `"| VP target: POC @ 1934.5 (2.1R)"` to the signal rationale.
On fallback: append `"| VP: no level ≥1R, using 2.5R multiple"`.

---

## Bar Sourcing

### At startup

After `broker.get_historical_bars(days=2)` (already called in the engine), feed those bars in order into `vp_tracker.on_bar()` before the live stream begins. This ensures the prior session profile is fully computed before London open.

### Live

Every bar passed to `StrategyRunner.on_bar()` is also passed to `runner.vp.on_bar()` in the engine's bar handler, immediately before strategy evaluation.

---

## Config Changes

Six new fields added to `StrategyParams` in `bot_config.py`:

```python
vp_enabled: bool = True
vp_tick_size: Decimal = Decimal("0.10")       # price quantization for bins
vp_value_area_pct: float = 0.70               # % of volume defining value area
vp_filter_tolerance: Decimal = Decimal("2.0") # price units outside VA still accepted
vp_hvn_threshold: float = 1.5                 # volume × mean to qualify as HVN
vp_min_target_r: Decimal = Decimal("1.0")     # minimum R a VP level must deliver
```

All fields are hot-applicable via `PATCH /api/config` — no restart required.
`vp_enabled: false` bypasses both filter and target override, reverting to current behavior.

---

## Engine Integration

`StrategyRunner` gains:

```python
vp: VolumeProfileTracker | None = None
```

In the engine's bar handler:

```python
if runner.vp:
    runner.vp.on_bar(bar)

signal = runner.on_bar(bar)

if signal and runner.vp and runner.vp.has_prior_profile() and cfg.strategy.vp_enabled:
    signal = runner.vp.apply(signal, cfg.strategy)  # None = filtered out
```

---

## Success Criteria

- A signal whose entry is clearly outside the prior session value area (and beyond tolerance) is dropped and logged at INFO level with reason
- A signal that passes the filter has its target replaced by the nearest VP level ≥ 1R; fallback to R-multiple if none qualifies
- On first day of running (no prior profile), all signals pass through unmodified
- `vp_enabled: false` in config produces identical behavior to pre-VP code
- `npm run build` clean, no TS errors (frontend config panel update needed for new fields)

---

## Out of Scope

- Current-session (intraday) profile — prior session only
- LVN breakout entries — VP is a filter/target tool only; sweep+displacement remains the sole entry trigger
- Tick data — bar-based uniform distribution is the approximation
- Frontend VP visualization — not in this spec
