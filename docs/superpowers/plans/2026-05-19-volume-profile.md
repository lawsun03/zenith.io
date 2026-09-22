# Volume Profile Filter + Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a prior-session volume profile layer that filters sweep+displacement signals to value-area entries and replaces the fixed R-multiple target with the nearest significant VP level.

**Architecture:** A new pure module `app/strategy/volume_profile.py` accumulates per-session bar volume into price bins, computes POC/VAH/VAL/HVNs at session close, and exposes `apply(signal, cfg)` which the engine calls between signal emission and the pretrade gate. The engine stores a `strategy_cfg` reference so VP params hot-apply without restart.

**Tech Stack:** Python 3.x, Decimal arithmetic, Pydantic v2, React/TypeScript, Tailwind CSS.

**Spec:** `docs/superpowers/specs/2026-05-19-volume-profile-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `app/strategy/volume_profile.py` | `VolumeProfile` dataclass, `_compute_profile`, `_filter`, `_pick_target`, `VolumeProfileTracker` |
| Create | `tests/test_volume_profile.py` | All VP unit tests |
| Modify | `app/bot_config.py` | 6 new `StrategyParams` fields |
| Modify | `app/execution/engine.py` | `strategy_cfg` field on engine; VP calls in `_handle_bar` |
| Modify | `app/main.py` | `VolumeProfileTracker` in `_build_runner`; live warm-up in `_async_main` |
| Modify | `app/api/server.py` | Hot-apply `strategy_cfg` on PATCH |
| Modify | `frontend/src/types.ts` | 6 new `StrategyConfig` fields |
| Modify | `frontend/src/components/ConfigPanel.tsx` | VP slider controls in `FIELDS` array |

---

## Task 1: Add VP Config Fields to StrategyParams

**Files:**
- Modify: `app/bot_config.py`
- Create: `tests/test_volume_profile.py` (initial, just config roundtrip)

- [ ] **Step 1: Write the failing test**

Create `tests/test_volume_profile.py`:

```python
"""Tests for volume profile: config, profile construction, filter, target."""
from decimal import Decimal
from datetime import date
import pytest


# ── Task 1: Config roundtrip ──────────────────────────────────────────────────

def test_vp_config_fields_round_trip(tmp_path):
    """New VP fields must survive a save/load cycle."""
    from app.bot_config import BotConfig, StrategyParams, save_bot_config, load_bot_config

    cfg = BotConfig(strategy=StrategyParams(
        vp_enabled=False,
        vp_tick_size=Decimal("0.20"),
        vp_value_area_pct=0.68,
        vp_filter_tolerance=Decimal("3.0"),
        vp_hvn_threshold=2.0,
        vp_min_target_r=Decimal("1.5"),
    ))
    p = tmp_path / "cfg.json"
    save_bot_config(cfg, p)
    loaded = load_bot_config(p)

    assert loaded.strategy.vp_enabled is False
    assert loaded.strategy.vp_tick_size == Decimal("0.20")
    assert loaded.strategy.vp_value_area_pct == pytest.approx(0.68)
    assert loaded.strategy.vp_filter_tolerance == Decimal("3.0")
    assert loaded.strategy.vp_hvn_threshold == pytest.approx(2.0)
    assert loaded.strategy.vp_min_target_r == Decimal("1.5")


def test_vp_config_defaults():
    """VP fields must have sensible defaults that don't break existing configs."""
    from app.bot_config import StrategyParams

    s = StrategyParams()
    assert s.vp_enabled is True
    assert s.vp_tick_size == Decimal("0.10")
    assert s.vp_value_area_pct == pytest.approx(0.70)
    assert s.vp_filter_tolerance == Decimal("2.0")
    assert s.vp_hvn_threshold == pytest.approx(1.5)
    assert s.vp_min_target_r == Decimal("1.0")
```

- [ ] **Step 2: Run test — expect ImportError or AttributeError**

```
pytest tests/test_volume_profile.py::test_vp_config_fields_round_trip tests/test_volume_profile.py::test_vp_config_defaults -v
```

Expected: FAIL — `StrategyParams` does not have `vp_enabled`.

- [ ] **Step 3: Add the six fields to StrategyParams**

In `app/bot_config.py`, add after the `trend_ema_period` line (line ~31):

```python
    trend_ema_period: int = 50  # 0 = disabled; N = only take signals with the N-bar EMA trend

    # Volume profile filter + target
    vp_enabled: bool = True
    vp_tick_size: Decimal = Decimal("0.10")       # price quantization for bins
    vp_value_area_pct: float = 0.70               # fraction of volume defining value area
    vp_filter_tolerance: Decimal = Decimal("2.0") # price units outside VA edge still accepted
    vp_hvn_threshold: float = 1.5                 # volume × mean to qualify as HVN
    vp_min_target_r: Decimal = Decimal("1.0")     # minimum R a VP level must deliver as target
```

Also update `save_bot_config` — the `strategy` dict already uses `{k: _conv(v) for k, v in config.strategy.model_dump().items()}` which handles all fields automatically. No change needed there.

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/test_volume_profile.py::test_vp_config_fields_round_trip tests/test_volume_profile.py::test_vp_config_defaults -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```
git add app/bot_config.py tests/test_volume_profile.py
git commit -m "feat: add VP config fields to StrategyParams"
```

---

## Task 2: VolumeProfile Dataclass + _compute_profile

**Files:**
- Create: `app/strategy/volume_profile.py` (partial — dataclass + compute function only)
- Modify: `tests/test_volume_profile.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_volume_profile.py`:

```python
# ── Task 2: Profile computation ───────────────────────────────────────────────

def test_compute_profile_none_on_empty_bins():
    """Empty bins must return None — no profile on a day with no bars."""
    from app.strategy.volume_profile import _compute_profile

    assert _compute_profile({}, date(2026, 5, 18), 0.70, 1.5) is None


def test_compute_profile_poc_is_highest_volume_bin():
    """POC must be the price level with the highest accumulated volume."""
    from app.strategy.volume_profile import _compute_profile

    bins = {Decimal(str(p)): 10 for p in range(1900, 1910)}
    bins[Decimal("1905")] = 100  # clear winner

    profile = _compute_profile(bins, date(2026, 5, 18), 0.70, 1.5)
    assert profile is not None
    assert profile.poc == Decimal("1905")


def test_compute_profile_value_area_contains_poc():
    """VAL <= POC <= VAH must always hold."""
    from app.strategy.volume_profile import _compute_profile

    bins = {Decimal(str(p)): 10 for p in range(1900, 1910)}
    bins[Decimal("1904")] = 80

    profile = _compute_profile(bins, date(2026, 5, 18), 0.70, 1.5)
    assert profile is not None
    assert profile.val <= profile.poc <= profile.vah


def test_compute_profile_value_area_covers_70_pct():
    """Bins between VAL and VAH must account for >= 70% of total volume."""
    from app.strategy.volume_profile import _compute_profile

    bins = {Decimal(str(p)): 10 for p in range(1900, 1910)}
    bins[Decimal("1905")] = 100  # total = 190

    profile = _compute_profile(bins, date(2026, 5, 18), 0.70, 1.5)
    assert profile is not None

    vol_in_va = sum(v for p, v in bins.items() if profile.val <= p <= profile.vah)
    total = sum(bins.values())
    assert vol_in_va / total >= 0.70


def test_compute_profile_hvns_above_threshold():
    """HVNs must be price levels where volume > mean * threshold."""
    from app.strategy.volume_profile import _compute_profile

    # 9 bins at 10, 1 bin at 50. mean = (9*10+50)/10 = 14. threshold=1.5 → cutoff=21.
    bins = {Decimal(str(p)): 10 for p in range(1900, 1910)}
    bins[Decimal("1905")] = 50

    profile = _compute_profile(bins, date(2026, 5, 18), 0.70, 1.5)
    assert profile is not None
    assert Decimal("1905") in profile.hvns
    assert Decimal("1904") not in profile.hvns


def test_compute_profile_session_date_preserved():
    """The session_date must be stored on the returned profile."""
    from app.strategy.volume_profile import _compute_profile

    bins = {Decimal("1900"): 100}
    d = date(2026, 5, 18)
    profile = _compute_profile(bins, d, 0.70, 1.5)
    assert profile is not None
    assert profile.session_date == d
```

- [ ] **Step 2: Run — expect ImportError**

```
pytest tests/test_volume_profile.py -k "compute_profile" -v
```

Expected: FAIL — `volume_profile` module does not exist.

- [ ] **Step 3: Create app/strategy/volume_profile.py with VolumeProfile + _compute_profile**

```python
"""
Volume profile filter and target selector.

Builds a prior-session volume profile from OHLC bars and provides two
services to the execution engine:
  1. Filter — reject signals whose FVG entry is clearly outside the prior
     session value area (plus a configurable tolerance band).
  2. Target — replace the fixed R-multiple target with the nearest VP level
     (POC, VAH/VAL, or HVN) that delivers at least the configured minimum R.

Design: pure module — bars in, profile state updated. No I/O, no broker
dependency. The engine wires this alongside the existing strategy stack.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR

from app.sim.events import Bar
from app.bot_config import StrategyParams
from app.strategy.composer import Signal

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class VolumeProfile:
    """Finalized single-session volume profile."""

    session_date: date
    poc: Decimal        # price level with highest volume
    vah: Decimal        # value area high (upper bound of value_area_pct of volume)
    val: Decimal        # value area low
    hvns: list[Decimal] # sorted high-volume nodes (volume > mean × hvn_threshold)
    total_volume: int


def _compute_profile(
    bins: dict[Decimal, int],
    session_date: date,
    value_area_pct: float,
    hvn_threshold: float,
) -> VolumeProfile | None:
    """
    Compute POC, VAH/VAL, and HVNs from accumulated bin data.

    Value area algorithm: start from POC, expand outward one bin at a
    time always taking the higher-volume neighbor (upper on ties), until
    cumulative volume >= total * value_area_pct.
    """
    if not bins:
        return None
    total = sum(bins.values())
    if total == 0:
        return None

    poc = max(bins, key=lambda p: bins[p])

    sorted_prices = sorted(bins.keys())
    poc_idx = sorted_prices.index(poc)

    included: set[Decimal] = {poc}
    cumulative = bins[poc]
    target_vol = total * value_area_pct

    lo_idx = poc_idx - 1
    hi_idx = poc_idx + 1

    while cumulative < target_vol:
        lo_vol = bins[sorted_prices[lo_idx]] if lo_idx >= 0 else -1
        hi_vol = bins[sorted_prices[hi_idx]] if hi_idx < len(sorted_prices) else -1

        if lo_vol < 0 and hi_vol < 0:
            break

        if hi_vol >= lo_vol:  # tie goes to upper
            included.add(sorted_prices[hi_idx])
            cumulative += hi_vol
            hi_idx += 1
        else:
            included.add(sorted_prices[lo_idx])
            cumulative += lo_vol
            lo_idx -= 1

    vah = max(included)
    val = min(included)

    mean_vol = total / len(bins)
    hvns = sorted(p for p, v in bins.items() if v > mean_vol * hvn_threshold)

    return VolumeProfile(
        session_date=session_date,
        poc=poc,
        vah=vah,
        val=val,
        hvns=hvns,
        total_volume=total,
    )
```

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/test_volume_profile.py -k "compute_profile" -v
```

Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```
git add app/strategy/volume_profile.py tests/test_volume_profile.py
git commit -m "feat: add VolumeProfile dataclass and _compute_profile"
```

---

## Task 3: VolumeProfileTracker — on_bar + Session Management

**Files:**
- Modify: `app/strategy/volume_profile.py`
- Modify: `tests/test_volume_profile.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_volume_profile.py`:

```python
# ── Task 3: Session management ────────────────────────────────────────────────

from datetime import datetime, timezone


def _bar(ts_utc: datetime, high: float, low: float, close: float, volume: int) -> "Bar":
    from app.sim.events import Bar
    return Bar(
        instrument="MGC", timeframe="1min", ts=ts_utc,
        open=Decimal(str(close)), high=Decimal(str(high)),
        low=Decimal(str(low)), close=Decimal(str(close)),
        volume=volume,
    )


def test_no_prior_profile_on_first_day():
    """Tracker must not have a prior profile until a UTC date boundary is crossed."""
    from app.strategy.volume_profile import VolumeProfileTracker
    from app.bot_config import StrategyParams

    tracker = VolumeProfileTracker()
    cfg = StrategyParams()

    tracker.on_bar(_bar(datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc), 1902, 1899, 1901, 100), cfg)
    tracker.on_bar(_bar(datetime(2026, 5, 18, 11, 0, tzinfo=timezone.utc), 1903, 1900, 1902, 80), cfg)

    assert not tracker.has_prior_profile()


def test_prior_profile_set_after_date_boundary():
    """Feeding a bar with a new UTC date must finalize the prior session."""
    from app.strategy.volume_profile import VolumeProfileTracker
    from app.bot_config import StrategyParams

    tracker = VolumeProfileTracker()
    cfg = StrategyParams()

    tracker.on_bar(_bar(datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc), 1902, 1899, 1901, 100), cfg)
    tracker.on_bar(_bar(datetime(2026, 5, 18, 11, 0, tzinfo=timezone.utc), 1903, 1900, 1902, 80), cfg)
    assert not tracker.has_prior_profile()

    # Day 2 bar triggers finalization of day 1.
    tracker.on_bar(_bar(datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc), 1904, 1901, 1903, 60), cfg)
    assert tracker.has_prior_profile()


def test_bins_accumulate_volume_for_current_session():
    """After feeding bars on the same day, bins must contain nonzero volume."""
    from app.strategy.volume_profile import VolumeProfileTracker
    from app.bot_config import StrategyParams

    tracker = VolumeProfileTracker()
    cfg = StrategyParams()

    tracker.on_bar(_bar(datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc), 1902, 1900, 1901, 200), cfg)
    assert sum(tracker._bins.values()) > 0


def test_bins_reset_after_session_boundary():
    """Current session bins must reset when a new UTC date is seen."""
    from app.strategy.volume_profile import VolumeProfileTracker
    from app.bot_config import StrategyParams

    tracker = VolumeProfileTracker()
    cfg = StrategyParams()

    tracker.on_bar(_bar(datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc), 1902, 1900, 1901, 200), cfg)
    old_vol = sum(tracker._bins.values())

    # New day — bins should reset, then accumulate only the new bar's volume.
    tracker.on_bar(_bar(datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc), 1905, 1903, 1904, 50), cfg)
    new_vol = sum(tracker._bins.values())

    assert new_vol < old_vol  # new session has only one bar's volume
```

- [ ] **Step 2: Run — expect ImportError on VolumeProfileTracker**

```
pytest tests/test_volume_profile.py -k "session" or "bins" -v
```

Expected: FAIL — `VolumeProfileTracker` not defined.

- [ ] **Step 3: Add VolumeProfileTracker class to volume_profile.py**

Append to `app/strategy/volume_profile.py` (after `_compute_profile`):

```python
class VolumeProfileTracker:
    """
    Per-instrument session tracker. Feed every bar via on_bar(); call
    apply() on any signal before the pretrade gate.

    Session boundary is detected by UTC date change in bar timestamps.
    On first day (no prior session yet), apply() is a no-op.
    """

    def __init__(self) -> None:
        self._bins: dict[Decimal, int] = {}
        self._session_date: date | None = None
        self._prior: VolumeProfile | None = None

    # ------------------------------------------------------------------
    # Bar ingestion
    # ------------------------------------------------------------------

    def on_bar(self, bar: Bar, cfg: StrategyParams) -> None:
        """Feed a closed bar. Detects session boundaries and accumulates volume."""
        bar_date = bar.ts.date()

        if self._session_date is None:
            self._session_date = bar_date
        elif bar_date != self._session_date:
            # Session ended — finalize prior profile.
            self._prior = _compute_profile(
                self._bins,
                self._session_date,
                cfg.vp_value_area_pct,
                cfg.vp_hvn_threshold,
            )
            if self._prior:
                log.info(
                    "VP: prior session %s — POC=%.2f VAH=%.2f VAL=%.2f HVNs=%d",
                    self._session_date,
                    float(self._prior.poc),
                    float(self._prior.vah),
                    float(self._prior.val),
                    len(self._prior.hvns),
                )
            self._bins = {}
            self._session_date = bar_date

        self._accumulate(bar, cfg.vp_tick_size)

    def _accumulate(self, bar: Bar, tick_size: Decimal) -> None:
        """Distribute bar volume uniformly across the high-low price range."""
        if bar.volume == 0:
            return

        # Quantize low and high to nearest tick boundary.
        low_bin = (bar.low / tick_size).to_integral_value(rounding=ROUND_FLOOR) * tick_size
        high_bin = (bar.high / tick_size).to_integral_value(rounding=ROUND_HALF_UP) * tick_size

        bins_in_range: list[Decimal] = []
        p = low_bin
        while p <= high_bin:
            bins_in_range.append(p)
            p += tick_size

        if not bins_in_range:
            return

        vol_per_bin = max(1, bar.volume // len(bins_in_range))
        for p in bins_in_range:
            self._bins[p] = self._bins.get(p, 0) + vol_per_bin

    # ------------------------------------------------------------------
    # Read-only
    # ------------------------------------------------------------------

    def has_prior_profile(self) -> bool:
        return self._prior is not None
```

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/test_volume_profile.py -k "session or bins" -v
```

Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```
git add app/strategy/volume_profile.py tests/test_volume_profile.py
git commit -m "feat: add VolumeProfileTracker session management"
```

---

## Task 4: Filter — _filter Function

**Files:**
- Modify: `app/strategy/volume_profile.py`
- Modify: `tests/test_volume_profile.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_volume_profile.py`:

```python
# ── Task 4: Filter ────────────────────────────────────────────────────────────

def _signal(side: str, entry: float, stop: float) -> "Signal":
    from app.strategy.composer import Signal
    return Signal(
        instrument="MGC", side=side,  # type: ignore[arg-type]
        entry=Decimal(str(entry)), stop=Decimal(str(stop)),
        target=Decimal("0"),
        created_at=datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc),
        killzone="ny_am", sweep_pattern="B_one_bar",
        sweep_extreme=Decimal(str(stop)),
        fvg_low=None, fvg_high=None,
        rationale="test signal",
    )


def _profile(poc: float, vah: float, val: float, hvns: list[float] | None = None) -> "VolumeProfile":
    from app.strategy.volume_profile import VolumeProfile
    return VolumeProfile(
        session_date=date(2026, 5, 18),
        poc=Decimal(str(poc)),
        vah=Decimal(str(vah)),
        val=Decimal(str(val)),
        hvns=[Decimal(str(h)) for h in (hvns or [])],
        total_volume=1000,
    )


def test_long_inside_value_area_passes():
    from app.strategy.volume_profile import _filter
    assert _filter(_signal("long", 1900, 1897), _profile(1900, 1905, 1895), Decimal("2.0")) is True


def test_long_within_tolerance_above_vah_passes():
    """Entry 1.0 above VAH is within tolerance=2.0 — must pass."""
    from app.strategy.volume_profile import _filter
    assert _filter(_signal("long", 1906, 1903), _profile(1900, 1905, 1895), Decimal("2.0")) is True


def test_long_above_vah_plus_tolerance_rejected():
    """Entry 2.1 above VAH exceeds tolerance=2.0 — buying into clear resistance."""
    from app.strategy.volume_profile import _filter
    assert _filter(_signal("long", 1907.1, 1904), _profile(1900, 1905, 1895), Decimal("2.0")) is False


def test_long_below_val_discount_zone_passes():
    """Entry below VAL on a long is a discount — should never be rejected."""
    from app.strategy.volume_profile import _filter
    assert _filter(_signal("long", 1890, 1888), _profile(1900, 1905, 1895), Decimal("2.0")) is True


def test_short_inside_value_area_passes():
    from app.strategy.volume_profile import _filter
    assert _filter(_signal("short", 1900, 1903), _profile(1900, 1905, 1895), Decimal("2.0")) is True


def test_short_above_vah_premium_zone_passes():
    """Short entry above VAH is a premium zone — should pass."""
    from app.strategy.volume_profile import _filter
    assert _filter(_signal("short", 1910, 1913), _profile(1900, 1905, 1895), Decimal("2.0")) is True


def test_short_below_val_minus_tolerance_rejected():
    """Entry 2.1 below VAL — shorting into clear support below value."""
    from app.strategy.volume_profile import _filter
    assert _filter(_signal("short", 1892.9, 1896), _profile(1900, 1905, 1895), Decimal("2.0")) is False
```

- [ ] **Step 2: Run — expect ImportError on _filter**

```
pytest tests/test_volume_profile.py -k "filter" -v
```

Expected: FAIL — `_filter` not defined.

- [ ] **Step 3: Add _filter to volume_profile.py**

Add after `_compute_profile` (before the `VolumeProfileTracker` class):

```python
def _filter(signal: Signal, profile: VolumeProfile, tolerance: Decimal) -> bool:
    """
    Return True if the signal should proceed, False to drop it.

    Rejects longs whose entry is clearly above the value area (buying
    resistance) and shorts clearly below it (selling support). The
    tolerance band softens the gate — "loose" by design.
    """
    entry = signal.entry
    if signal.side == "long" and entry > profile.vah + tolerance:
        return False
    if signal.side == "short" and entry < profile.val - tolerance:
        return False
    return True
```

- [ ] **Step 4: Run tests — expect PASS**

```
pytest tests/test_volume_profile.py -k "filter" -v
```

Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```
git add app/strategy/volume_profile.py tests/test_volume_profile.py
git commit -m "feat: add VP filter (_filter) — loose value area gate"
```

---

## Task 5: Target Selection — _pick_target + apply

**Files:**
- Modify: `app/strategy/volume_profile.py`
- Modify: `tests/test_volume_profile.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_volume_profile.py`:

```python
# ── Task 5: Target selection + apply ─────────────────────────────────────────

def test_long_target_picks_nearest_vp_level_above_entry():
    """
    For a long, the nearest VP level above entry that clears min_r must win.
    POC at 1900 is only 2/3 R away (< 1.0R), so it's skipped.
    HVN at 1905 is 7/3 R — picked.
    """
    from app.strategy.volume_profile import _pick_target
    from app.bot_config import StrategyParams

    # entry=1898, stop=1895 → R=3
    prof = _profile(poc=1900, vah=1910, val=1890, hvns=[1905, 1915])
    cfg = StrategyParams(vp_min_target_r=Decimal("1.0"), r_multiple=Decimal("2.5"))
    sig = _signal("long", 1898, 1895)

    target, label = _pick_target(sig, prof, cfg)
    assert target == Decimal("1905")
    assert "HVN" in label


def test_long_target_fallback_when_no_level_clears_min_r():
    """
    If no VP level above entry delivers >= min_r, fall back to r_multiple.
    entry=1898, stop=1895, R=3, vah=1899 delivers only 1/3R < 1.0R → fallback.
    """
    from app.strategy.volume_profile import _pick_target
    from app.bot_config import StrategyParams

    prof = _profile(poc=1892, vah=1899, val=1888)
    cfg = StrategyParams(vp_min_target_r=Decimal("1.0"), r_multiple=Decimal("2.5"))
    sig = _signal("long", 1898, 1895)

    target, label = _pick_target(sig, prof, cfg)
    assert target == Decimal("1898") + Decimal("3") * Decimal("2.5")
    assert "no VP level" in label


def test_short_target_picks_nearest_vp_level_below_entry():
    """
    For a short, nearest level below entry that clears min_r.
    entry=1902, stop=1905, R=3. VAL=1895 is 7/3R ≥ 1.0R → picked.
    """
    from app.strategy.volume_profile import _pick_target
    from app.bot_config import StrategyParams

    prof = _profile(poc=1908, vah=1912, val=1895)
    cfg = StrategyParams(vp_min_target_r=Decimal("1.0"), r_multiple=Decimal("2.5"))
    sig = _signal("short", 1902, 1905)

    target, label = _pick_target(sig, prof, cfg)
    assert target == Decimal("1895")
    assert "VAL" in label


def test_apply_returns_none_when_filtered():
    """apply() must return None when the filter rejects the signal."""
    from app.strategy.volume_profile import VolumeProfileTracker
    from app.bot_config import StrategyParams

    tracker = VolumeProfileTracker()
    cfg = StrategyParams()

    # Inject a prior profile directly.
    from app.strategy.volume_profile import VolumeProfile
    tracker._prior = VolumeProfile(
        session_date=date(2026, 5, 18),
        poc=Decimal("1900"), vah=Decimal("1905"), val=Decimal("1895"),
        hvns=[], total_volume=1000,
    )

    # Long entry at 1910 — 5 above VAH (1905), tolerance 2.0 → rejected.
    sig = _signal("long", 1910, 1907)
    result = tracker.apply(sig, cfg)
    assert result is None


def test_apply_replaces_target_and_appends_rationale():
    """apply() must return a new Signal with VP-derived target and updated rationale."""
    from app.strategy.volume_profile import VolumeProfileTracker, VolumeProfile
    from app.bot_config import StrategyParams

    tracker = VolumeProfileTracker()
    cfg = StrategyParams(vp_min_target_r=Decimal("1.0"), r_multiple=Decimal("2.5"))
    tracker._prior = VolumeProfile(
        session_date=date(2026, 5, 18),
        poc=Decimal("1900"), vah=Decimal("1910"), val=Decimal("1890"),
        hvns=[Decimal("1905")], total_volume=1000,
    )

    # entry=1898, stop=1895, R=3. HVN=1905 is 7/3R ≥ 1.0 → target=1905.
    sig = _signal("long", 1898, 1895)
    result = tracker.apply(sig, cfg)

    assert result is not None
    assert result.target == Decimal("1905")
    assert "VP" in result.rationale
    assert result.rationale != sig.rationale  # rationale was updated


def test_apply_passthrough_when_no_prior_profile():
    """apply() must return the signal unchanged when no prior profile exists."""
    from app.strategy.volume_profile import VolumeProfileTracker
    from app.bot_config import StrategyParams

    tracker = VolumeProfileTracker()
    assert not tracker.has_prior_profile()

    sig = _signal("long", 1900, 1897)
    result = tracker.apply(sig, StrategyParams())
    assert result is sig  # exact same object, untouched
```

- [ ] **Step 2: Run — expect ImportError on _pick_target / apply**

```
pytest tests/test_volume_profile.py -k "target or apply" -v
```

Expected: FAIL.

- [ ] **Step 3: Add _pick_target and VolumeProfileTracker.apply to volume_profile.py**

Add `_pick_target` after `_filter`:

```python
def _pick_target(
    signal: Signal,
    profile: VolumeProfile,
    cfg: StrategyParams,
) -> tuple[Decimal, str]:
    """
    Return (target_price, label) for the signal.

    Scans VP levels on the correct side of entry, sorted nearest-first.
    Picks the first one that delivers >= vp_min_target_r of R.
    Falls back to cfg.r_multiple if no level qualifies.
    """
    entry = signal.entry
    stop = signal.stop
    min_r = cfg.vp_min_target_r

    if signal.side == "long":
        r = entry - stop
        candidates: list[tuple[Decimal, str]] = []
        if profile.poc > entry:
            candidates.append((profile.poc, f"POC @ {profile.poc}"))
        if profile.vah > entry:
            candidates.append((profile.vah, f"VAH @ {profile.vah}"))
        for hvn in profile.hvns:
            if hvn > entry:
                candidates.append((hvn, f"HVN @ {hvn}"))
        candidates.sort(key=lambda x: x[0])  # nearest first

        for level, label in candidates:
            if r > 0 and (level - entry) >= r * min_r:
                actual_r = (level - entry) / r
                return level, f"{label} ({actual_r:.1f}R)"

        fallback = entry + r * cfg.r_multiple
        return fallback, f"no VP level ≥{min_r}R, using {cfg.r_multiple}R multiple"

    else:  # short
        r = stop - entry
        candidates = []
        if profile.poc < entry:
            candidates.append((profile.poc, f"POC @ {profile.poc}"))
        if profile.val < entry:
            candidates.append((profile.val, f"VAL @ {profile.val}"))
        for hvn in profile.hvns:
            if hvn < entry:
                candidates.append((hvn, f"HVN @ {hvn}"))
        candidates.sort(key=lambda x: x[0], reverse=True)  # nearest first

        for level, label in candidates:
            if r > 0 and (entry - level) >= r * min_r:
                actual_r = (entry - level) / r
                return level, f"{label} ({actual_r:.1f}R)"

        fallback = entry - r * cfg.r_multiple
        return fallback, f"no VP level ≥{min_r}R, using {cfg.r_multiple}R multiple"
```

Add `apply` method to `VolumeProfileTracker` (after `has_prior_profile`):

```python
    def apply(self, signal: Signal, cfg: StrategyParams) -> Signal | None:
        """
        Apply VP filter and target override to a signal.

        Returns None if the filter rejects the signal (logged at INFO).
        Returns a new Signal (dataclasses.replace) with VP-derived target
        and updated rationale. Returns the original signal unchanged if no
        prior profile is available (first day / historical fetch failed).
        """
        if not self._prior:
            return signal

        if not _filter(signal, self._prior, cfg.vp_filter_tolerance):
            log.info(
                "VP filter: rejected %s entry=%.2f outside value area "
                "(VAL=%.2f VAH=%.2f tol=%.2f)",
                signal.side, float(signal.entry),
                float(self._prior.val), float(self._prior.vah),
                float(cfg.vp_filter_tolerance),
            )
            return None

        new_target, target_label = _pick_target(signal, self._prior, cfg)
        new_rationale = signal.rationale + f" | VP: {target_label}"
        return dataclasses.replace(signal, target=new_target, rationale=new_rationale)
```

- [ ] **Step 4: Run all VP tests**

```
pytest tests/test_volume_profile.py -v
```

Expected: all PASS (all tasks so far).

- [ ] **Step 5: Commit**

```
git add app/strategy/volume_profile.py tests/test_volume_profile.py
git commit -m "feat: add _pick_target and VolumeProfileTracker.apply"
```

---

## Task 6: Engine Integration

**Files:**
- Modify: `app/execution/engine.py` — add `strategy_cfg` field; VP calls in `_handle_bar`
- Modify: `app/main.py` — add `vp` to `_build_runner`; pass `strategy_cfg` to engine
- Modify: `app/api/server.py` — hot-apply `strategy_cfg` on PATCH

No new unit tests (integration covered by existing tests + VP unit tests). Run full suite to verify nothing is broken.

- [ ] **Step 1: Add `vp` to StrategyRunner in engine.py**

In `app/execution/engine.py`, find the `StrategyRunner` dataclass (~line 83). Add `vp` as the last field:

```python
@dataclass
class StrategyRunner:
    instrument: str
    liquidity: LiquidityTracker
    displacement: DisplacementDetector
    composer: SweepDisplacementComposer
    vp: "VolumeProfileTracker | None" = None  # set in _build_runner if vp_enabled
```

Add the import at the top of `engine.py`:

```python
from app.strategy.volume_profile import VolumeProfileTracker
```

- [ ] **Step 2: Add `strategy_cfg` to ExecutionEngine.__init__**

In `ExecutionEngine.__init__`, add a parameter and store it (after `contracts`):

```python
    def __init__(
        self,
        broker: Broker,
        risk_state: RiskState,
        runners: list[StrategyRunner],
        on_signal: SignalEmitted | None = None,
        on_order_placed: Callable[[], None] | None = None,
        replay_mode: bool = False,
        contracts: int = 1,
        strategy_cfg: "StrategyParams | None" = None,
    ) -> None:
        ...
        self.contracts = contracts
        self.strategy_cfg = strategy_cfg  # used by VP gate; hot-applied via PATCH /api/config
```

Add the import at the top:

```python
from app.bot_config import StrategyParams
```

- [ ] **Step 3: Add VP calls to _handle_bar**

In `_handle_bar` (~line 191), insert VP on_bar call before `runner.on_bar(bar)`, and VP gate after:

```python
    async def _handle_bar(self, bar: Bar) -> None:
        runner = self.runners.get(bar.instrument)
        if runner is None:
            return

        if self._replay_mode:
            is_stale = False
        else:
            tf_secs = _tf_seconds(bar.timeframe)
            age_secs = (datetime.now(timezone.utc) - bar.ts).total_seconds()
            is_stale = age_secs > 2 * tf_secs

        # Feed bar into VP tracker regardless of staleness — warmup bars
        # still build the profile; the profile is never stale.
        if runner.vp is not None and self.strategy_cfg is not None:
            runner.vp.on_bar(bar, self.strategy_cfg)

        try:
            signal = runner.on_bar(bar)
        except Exception:
            log.exception("Strategy raised on bar %s", bar.ts)
            return

        if signal is None or is_stale:
            if is_stale and signal is not None:
                log.debug(
                    "Warmup bar %s (age=%.0fs) generated signal — skipping order.",
                    bar.ts, (datetime.now(timezone.utc) - bar.ts).total_seconds(),
                )
            return

        # VP gate: filter + target override. Runs before pretrade risk check.
        if (
            runner.vp is not None
            and self.strategy_cfg is not None
            and self.strategy_cfg.vp_enabled
            and runner.vp.has_prior_profile()
        ):
            signal = runner.vp.apply(signal, self.strategy_cfg)
            if signal is None:
                return  # VP filter rejected — already logged in apply()

        outcome = await self._act_on_signal(signal)
        ...  # rest of handler unchanged
```

- [ ] **Step 4: Add `vp` to `_build_runner` in main.py**

In `app/main.py`, add the import:

```python
from app.strategy.volume_profile import VolumeProfileTracker
```

Update `_build_runner` to instantiate the tracker:

```python
def _build_runner(
    instrument: str,
    s: StrategyParams,
    enabled_killzones: list[str] | None = None,
) -> StrategyRunner:
    zones = killzones_from_names(enabled_killzones) if enabled_killzones else None
    return StrategyRunner(
        instrument=instrument,
        liquidity=LiquidityTracker(LiquidityConfig(
            swing_lookback=s.swing_lookback,
            min_penetration=s.min_penetration,
            multi_bar_window=s.multi_bar_window,
            max_swings=50,
        )),
        displacement=DisplacementDetector(DisplacementConfig(
            atr_period=s.atr_period,
            body_atr_multiple=s.body_atr_multiple,
            min_body_to_range_ratio=s.min_body_to_range_ratio,
            min_absolute_body=s.min_absolute_body,
        )),
        composer=SweepDisplacementComposer(ComposerConfig(
            instrument=instrument,
            displacement_window_bars=s.displacement_window_bars,
            stop_buffer=s.stop_buffer,
            r_multiple=s.r_multiple,
            killzones=zones,
            trend_ema_period=s.trend_ema_period,
        )),
        vp=VolumeProfileTracker(),
    )
```

- [ ] **Step 5: Pass strategy_cfg to ExecutionEngine in main.py**

In `_async_main`, find the `ExecutionEngine(...)` constructor call (~line 605). Add `strategy_cfg`:

```python
    engine = ExecutionEngine(
        broker=broker,
        risk_state=risk_state,
        runners=[runner],
        on_signal=_make_signal_journaler(journal, notifier),
        on_order_placed=reconciler.notify_order_placed,
        contracts=bot_cfg.contracts,
        strategy_cfg=bot_cfg.strategy,
    )
```

Also update the restart block in `_run_paper` (~line 431) to refresh strategy_cfg:

```python
        if engine is not None:
            new_cfg = load_bot_config(cfg.bot_config_path)
            new_runner = _build_runner(cfg.instrument, new_cfg.strategy, new_cfg.enabled_killzones)
            engine.runners = {cfg.instrument: new_runner}
            engine.strategy_cfg = new_cfg.strategy  # ← add this line
```

- [ ] **Step 6: Hot-apply strategy_cfg in server.py**

In `app/api/server.py`, find the PATCH `/api/config` handler. Read that handler first to understand the existing hot-apply pattern, then add at the end of the hot-apply block:

```python
        # VP config: apply() reads strategy_cfg at call time — no other action needed.
        if _engine is not None:
            _engine.strategy_cfg = new_cfg.strategy
```

- [ ] **Step 7: Run existing tests to verify nothing is broken**

```
pytest tests/ -v
```

Expected: all existing tests PASS.

- [ ] **Step 8: Commit**

```
git add app/execution/engine.py app/main.py app/api/server.py
git commit -m "feat: wire VolumeProfileTracker into engine bar handler"
```

---

## Task 7: Live Mode VP Warm-Up (Historical Bars)

**Files:**
- Modify: `app/main.py`

For paper mode: the CSV replay spans multiple UTC dates, so the VP tracker warms up naturally as bars cross date boundaries. No extra code needed.

For live mode: `_run_live` connects and immediately starts receiving live bars — the VP tracker has no prior session data on day 1. Fix: fetch 2 days of historical bars immediately after broker connection and before the live stream delivers bars, feeding them directly into the runner's VP tracker.

- [ ] **Step 1: Add VP warm-up to _async_main (live mode)**

In `app/main.py`, find where `_run_live` is called (near the bottom of `_async_main`). Before `_run_live(...)`, add:

```python
    # VP warm-up for live mode: feed prior session bars before live stream begins.
    # Paper mode warms up naturally via the CSV replay spanning multiple dates.
    if cfg.mode == "live" and runner.vp is not None:
        await _warm_up_vp(broker, runner, bot_cfg)
```

Add the helper function before `_async_main`:

```python
async def _warm_up_vp(broker: "Broker", runner: "StrategyRunner", bot_cfg: BotConfig) -> None:
    """
    Fetch 2 days of historical bars and feed them into the VP tracker.

    This ensures the prior session profile is ready before the first live
    bar arrives. Called once at startup in live mode, after broker connect.
    If the fetch fails (network, SDK), the tracker starts without a prior
    profile and filters are bypassed (has_prior_profile() returns False).
    """
    from app.sim.topstepx import TopstepXBroker
    if not isinstance(broker, TopstepXBroker):
        return

    timeframe = (bot_cfg.timeframes[0] if bot_cfg.timeframes else "1min")
    try:
        bars = await broker.get_historical_bars(timeframe=timeframe, days=2)
    except Exception:
        log.warning("VP warm-up: historical bar fetch failed — VP filter inactive today")
        return

    if not bars:
        log.warning("VP warm-up: no historical bars returned — VP filter inactive today")
        return

    assert runner.vp is not None
    for bar in bars:
        runner.vp.on_bar(bar, bot_cfg.strategy)

    log.info(
        "VP warm-up complete: %d bars fed, prior profile=%s",
        len(bars),
        runner.vp.has_prior_profile(),
    )
```

- [ ] **Step 2: Verify broker.subscribe is called before _warm_up_vp**

The broker must be subscribed (connected) before `get_historical_bars` is called. In `_async_main`, the call order must be:

```
await broker.connect(...)     ← already exists
await broker.subscribe(...)   ← already in _run_live, move subscribe call earlier
_warm_up_vp(...)
_run_live(...)                ← simplified to just await shutdown.wait()
```

Read `_async_main` from line ~605 onward and `_run_live` to check whether `broker.subscribe` needs to be hoisted. If `broker.subscribe` is inside `_run_live`, move it to `_async_main` before the warm-up call so the broker is connected when `get_historical_bars` is called.

- [ ] **Step 3: Run the full test suite**

```
pytest tests/ -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```
git add app/main.py
git commit -m "feat: VP warm-up from historical bars on live startup"
```

---

## Task 8: Frontend — Types + Config Panel

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/components/ConfigPanel.tsx`

- [ ] **Step 1: Add VP fields to StrategyConfig in types.ts**

In `frontend/src/types.ts`, add 6 fields to the `StrategyConfig` interface. `Decimal` fields come as strings over the API; numeric fields as numbers; bool as boolean:

```typescript
export interface StrategyConfig {
  swing_lookback: number
  min_penetration: string
  multi_bar_window: number
  atr_period: number
  body_atr_multiple: string
  min_body_to_range_ratio: string
  min_absolute_body: string
  displacement_window_bars: number
  stop_buffer: string
  r_multiple: string
  trend_ema_period: number
  // Volume profile
  vp_enabled: boolean
  vp_tick_size: string
  vp_value_area_pct: number
  vp_filter_tolerance: string
  vp_hvn_threshold: number
  vp_min_target_r: string
}
```

- [ ] **Step 2: Add VP sliders to FIELDS array in ConfigPanel.tsx**

Read `ConfigPanel.tsx` past line 80 to find where the `FIELDS` array ends. The existing pattern is a `FieldDef` object with `key`, `label`, `type: 'slider'`, `section: 'strategy'`, `min`, `max`, `step`, `hint`. Add at the end of the array (before the closing `]`):

```typescript
  {
    key: 'trend_ema_period', label: 'Trend EMA Period', type: 'slider', section: 'strategy',
    min: 0, max: 200, step: 1,
    hint: '0 = disabled. When active, long signals require close > EMA, shorts require close < EMA. Warmup: filter inactive until N bars seen.',
  },
  {
    key: 'vp_value_area_pct', label: 'VP Value Area %', type: 'slider', section: 'strategy',
    min: 0.5, max: 0.9, step: 0.01,
    hint: 'Fraction of prior session volume defining the value area. 0.70 = the standard 70% rule. Higher = wider area, more signals pass.',
  },
  {
    key: 'vp_filter_tolerance', label: 'VP Tolerance ($)', type: 'slider', section: 'strategy',
    min: 0, max: 10, step: 0.1,
    hint: 'Price units outside the value area edge that still pass the filter. 0 = strict (inside VA only). 2.0 = loose (20 ticks beyond edge accepted).',
  },
  {
    key: 'vp_hvn_threshold', label: 'VP HVN Threshold', type: 'slider', section: 'strategy',
    min: 1.0, max: 4.0, step: 0.1,
    hint: 'A price level is a High Volume Node if its volume > mean × this value. Higher = fewer, more significant HVNs. Try 1.5–2.0.',
  },
  {
    key: 'vp_min_target_r', label: 'VP Min Target R', type: 'slider', section: 'strategy',
    min: 0.5, max: 3.0, step: 0.1,
    hint: 'A VP level must deliver at least this many R to be used as target. Too close levels are skipped; falls back to r_multiple if none qualify.',
  },
```

Note: `vp_enabled` (bool) and `vp_tick_size` (Decimal) require `type: 'select'` or a toggle — check the existing rendering logic in ConfigPanel to see how booleans and fixed-choice fields are handled. If there is no existing toggle pattern, add `vp_enabled` as a `select` field with options `['true', 'false']` or skip it (it can be edited in `bot_config.json` directly).

- [ ] **Step 3: Build frontend and fix any TypeScript errors**

```
cd frontend && npm run build
```

Expected: clean build, no TS errors. Fix any type errors that appear (usually a missing field in a default object or a type assertion).

- [ ] **Step 4: Start dev server and verify VP controls appear**

```
cd frontend && npm run dev
```

Open `http://localhost:5173`, open the Config panel, scroll to the Strategy section. Verify the four VP sliders appear with correct labels and hint text.

- [ ] **Step 5: Commit**

```
git add frontend/src/types.ts frontend/src/components/ConfigPanel.tsx
git commit -m "feat: add VP config fields to frontend types and ConfigPanel"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task that implements it |
|-----------------|------------------------|
| Prior-session profile from OHLC bars | Task 2 (_compute_profile), Task 3 (on_bar + _accumulate) |
| Session boundary by UTC date | Task 3 (session_date check in on_bar) |
| POC, VAH/VAL, HVNs | Task 2 (_compute_profile) |
| Loose filter — entry inside/near value area | Task 4 (_filter) |
| Filter direction edge cases (long below VAL = pass, short above VAH = pass) | Task 4 tests |
| Target = nearest VP level ≥ min_r | Task 5 (_pick_target) |
| Fallback to r_multiple if no VP level qualifies | Task 5 (_pick_target fallback) |
| apply() returns None on filter reject | Task 5 (apply + test) |
| apply() passthrough when no prior profile | Task 5 (test_apply_passthrough...) |
| Signal rationale updated with VP info | Task 5 (apply — appends VP label) |
| Engine wiring: vp.on_bar before runner.on_bar | Task 6 (_handle_bar) |
| VP gate between signal emission and pretrade | Task 6 (_handle_bar) |
| strategy_cfg hot-apply via PATCH /api/config | Task 6 (server.py step) |
| vp_enabled=False reverts to pre-VP behavior | Task 6 (if vp_enabled guard in _handle_bar) |
| Historical bar warm-up for live mode | Task 7 (_warm_up_vp) |
| 6 new config fields in StrategyParams | Task 1 |
| Frontend types + config controls | Task 8 |

All spec requirements covered. ✓

**Type consistency check:**
- `VolumeProfileTracker.on_bar(bar: Bar, cfg: StrategyParams)` — consistent across Tasks 3, 6, 7
- `VolumeProfileTracker.apply(signal: Signal, cfg: StrategyParams) -> Signal | None` — consistent across Tasks 5, 6
- `_filter(signal: Signal, profile: VolumeProfile, tolerance: Decimal) -> bool` — consistent Tasks 4, 5
- `_pick_target(signal: Signal, profile: VolumeProfile, cfg: StrategyParams) -> tuple[Decimal, str]` — consistent Task 5

**Placeholder scan:** None found. All steps include complete code. ✓
