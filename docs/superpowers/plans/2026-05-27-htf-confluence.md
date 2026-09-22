# HTF Confluence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 4-hour directional bias filtering and higher-timeframe target selection (4h FVG → 30min swing) to the sweep+displacement strategy, both opt-in and defaulting off.

**Architecture:** A new pure module `app/strategy/htf.py` holds two trackers (`HTFBiasTracker`, `HTFLevelFinder`) fed by REST-polled 4h/30min bars — never the single-timeframe live bar pipeline. The trackers are owned by the `ExecutionEngine` (not the per-instrument runner, so they survive `/api/strategy/reload`) and refreshed by a background task in `main.py`. The engine reads them at signal-decision time, gating direction (Part A) and overriding the target (Part B). Config flags hot-apply through the existing `_engine.strategy_cfg` assignment.

**Tech Stack:** Python 3.12, pytest, FastAPI, React/TypeScript. Reuses existing `LiquidityTracker` (swing detection) and FVG geometry from `displacement.py`.

---

## Background for the implementer (read once)

- **Why REST, not subscribe():** `app/sim/topstepx.py` `_on_new_bar` hardcodes `tf_list[0]` — only the first subscribed timeframe is fetched and fanned out, and `Bar` events carry no routing tag. Adding `"4h"`/`"30min"` to `subscribe()` would NOT deliver them. The order path is real money (CLAUDE.md Rule 8) — do not touch NEW_BAR routing.
- **Timeframe strings:** `_parse_timeframe` reads a trailing `h` as hours, `min` as minutes. Use `"4h"` and `"30min"`. **`"4hr"` silently falls back to 1min** — never use it.
- **Existing pattern to mirror:** `_warm_up_vp` in `app/main.py` (around line 622) fetches historical bars via `broker.get_historical_bars(timeframe=..., days=..., limit=...)` and feeds a tracker at startup. The HTF warm-up and refresh use the same call.
- **Signal pipeline today** (`app/execution/engine.py` `on_bar`, lines ~300–346): feed VP → `runner.on_bar` → if VP enabled+prior, `runner.vp.apply(signal)` (filters AND overrides target) → `_act_on_signal`. We split VP's filter from its target selection and insert HTF logic around it.
- **Bar type:** `app/sim/events.py` `Bar` has `instrument, timeframe, ts, open, high, low, close, volume` (all Decimal except ts/volume/instrument/timeframe).
- **Run tests with the venv python:** `.venv/Scripts/python.exe -m pytest ...` (plain `python` lacks pydantic).

---

## File Structure

- **Create** `app/strategy/htf.py` — `HTFBiasTracker` + `HTFLevelFinder` (pure, bars-in/state-out).
- **Create** `tests/test_htf.py` — unit tests for both trackers.
- **Modify** `app/strategy/volume_profile.py` — split `apply()` into public `passes_filter()` + `select_target()`; `apply()` delegates (no behavior change).
- **Modify** `app/bot_config.py` — six new `StrategyParams` fields.
- **Modify** `app/execution/engine.py` — engine HTF attributes + bias gate + VP-bypass + target precedence in `on_bar`.
- **Modify** `tests/test_engine.py` — integration tests (bias gate, bypass regression, target precedence, off=unchanged).
- **Modify** `app/main.py` — `_warm_up_htf`, `_htf_refresh_loop`, lifecycle wiring, attach trackers to engine.
- **Modify** `frontend/src/types.ts` — six new strategy fields.
- **Modify** `frontend/src/components/ConfigPanel.tsx` — six new config rows + form serialization.

---

## Task 1: Split VP filter from target selection (enabling refactor)

**Files:**
- Modify: `app/strategy/volume_profile.py:265-292`
- Test: `tests/test_volume_profile.py` (existing tests must stay green)

- [ ] **Step 1: Run existing VP tests to capture green baseline**

Run: `.venv/Scripts/python.exe -m pytest tests/test_volume_profile.py -q`
Expected: PASS (note the count; it must not drop after the refactor).

- [ ] **Step 2: Add two public methods and make `apply` delegate**

Replace the `apply` method (lines 268-292) with:

```python
    def passes_filter(self, signal: Signal, cfg: StrategyParams) -> bool:
        """
        True if the signal passes the VP value-area filter (or there is no
        prior profile to filter against). Pure check — no target mutation.
        """
        if not self._prior:
            return True
        return _filter(signal, self._prior, cfg.vp_filter_tolerance)

    def select_target(self, signal: Signal, cfg: StrategyParams) -> Signal | None:
        """
        Return a copy of `signal` with the VP-derived target and an updated
        rationale, or None if there is no prior profile. Does NOT filter.
        """
        if not self._prior:
            return None
        new_target, target_label = _pick_target(signal, self._prior, cfg)
        new_rationale = signal.rationale + f" | VP: {target_label}"
        return dataclasses.replace(signal, target=new_target, rationale=new_rationale)

    def apply(self, signal: Signal, cfg: StrategyParams) -> Signal | None:
        """
        Apply VP filter and target override to a signal.

        Returns None if the filter rejects the signal (logged at INFO).
        Returns a new Signal with VP-derived target. Returns the original
        signal unchanged if no prior profile is available.
        """
        if not self._prior:
            return signal

        if not self.passes_filter(signal, cfg):
            log.info(
                "VP filter: rejected %s entry=%.2f outside value area "
                "(VAL=%.2f VAH=%.2f tol=%.2f)",
                signal.side, float(signal.entry),
                float(self._prior.val), float(self._prior.vah),
                float(cfg.vp_filter_tolerance),
            )
            return None

        return self.select_target(signal, cfg)
```

- [ ] **Step 3: Run VP tests to verify no regression**

Run: `.venv/Scripts/python.exe -m pytest tests/test_volume_profile.py -q`
Expected: PASS, same count as Step 1.

- [ ] **Step 4: Commit**

```bash
git add app/strategy/volume_profile.py
git commit -m "refactor: split VP apply() into passes_filter() + select_target()

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: HTFBiasTracker (pure module)

**Files:**
- Create: `app/strategy/htf.py`
- Test: `tests/test_htf.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_htf.py`:

```python
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from app.sim.events import Bar
from app.strategy.htf import HTFBiasTracker, HTFLevelFinder


def _bar(i: int, o, h, l, c, tf="4h") -> Bar:
    return Bar(
        instrument="MGC",
        timeframe=tf,
        ts=datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(hours=4 * i),
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)), volume=100,
    )


def _swing_sequence(highs_lows: list[tuple[float, float]]) -> list[Bar]:
    """Build bars that confirm a swing high then low alternately.

    Each (h, l) pair becomes a pivot bar surrounded by lower-high / higher-low
    neighbors so a lookback=2 swing confirms. Neighbors are 0.5 inside the pivot.
    """
    bars: list[Bar] = []
    i = 0
    for h, l in highs_lows:
        # neighbor below, pivot, neighbor below → confirms swing HIGH at pivot
        bars.append(_bar(i, h - 1, h - 0.5, l + 0.5, l + 0.6)); i += 1
        bars.append(_bar(i, l + 0.6, h, l, h - 0.2)); i += 1            # pivot
        bars.append(_bar(i, h - 0.2, h - 0.5, l + 0.5, l + 0.6)); i += 1
    return bars


def test_bias_neutral_when_insufficient_data():
    t = HTFBiasTracker(lookback=2)
    t.rebuild([_bar(0, 10, 11, 9, 10)])
    assert t.bias() == "neutral"


def test_bias_bullish_on_higher_highs_and_higher_lows():
    # Two rising pivots: (12,8) then (14,10) → HH + HL → bullish
    t = HTFBiasTracker(lookback=2)
    t.rebuild(_swing_sequence([(12, 8), (14, 10)]))
    assert t.bias() == "bullish"


def test_bias_bearish_on_lower_highs_and_lower_lows():
    t = HTFBiasTracker(lookback=2)
    t.rebuild(_swing_sequence([(14, 10), (12, 8)]))
    assert t.bias() == "bearish"


def test_bias_neutral_on_mixed_structure():
    # HH but LL → mixed → neutral
    t = HTFBiasTracker(lookback=2)
    t.rebuild(_swing_sequence([(12, 10), (14, 8)]))
    assert t.bias() == "neutral"
```

- [ ] **Step 2: Run to verify failure (import error)**

Run: `.venv/Scripts/python.exe -m pytest tests/test_htf.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.strategy.htf'`.

- [ ] **Step 3: Implement `HTFBiasTracker` (and module skeleton)**

Create `app/strategy/htf.py`:

```python
"""
Higher-timeframe (HTF) confluence trackers.

Pure modules: bars in, state out. No I/O, no broker. Fed by REST-polled
4h / 30min bars (the live 1min pipeline is single-timeframe — see
main.py._htf_refresh_loop). The execution engine reads these at
signal-decision time.

  - HTFBiasTracker:  4h swing structure → bullish / bearish / neutral.
  - HTFLevelFinder:  4h FVGs + 30min swings → a take-profit target price.

Both reuse the existing LiquidityTracker for swing detection so the swing
convention matches the rest of the strategy.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Literal

from app.sim.events import Bar
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker

log = logging.getLogger(__name__)

Bias = Literal["bullish", "bearish", "neutral"]


class HTFBiasTracker:
    """4h swing structure → directional bias.

    bullish = most recent confirmed structure is higher-high AND higher-low.
    bearish = lower-high AND lower-low. Anything else (mixed, or fewer than
    two confirmed swings of each kind) is neutral — fail-open so the gate is
    inert until real structure exists.
    """

    def __init__(self, lookback: int = 3) -> None:
        self._lookback = lookback
        self._bias: Bias = "neutral"

    def rebuild(self, bars: list[Bar]) -> None:
        """Recompute bias from the full bar list (called on each refresh)."""
        tracker = LiquidityTracker(LiquidityConfig(
            swing_lookback=self._lookback,
            max_swings=50,
        ))
        for bar in bars:
            tracker.on_bar(bar)

        highs = tracker.recent_high_swings
        lows = tracker.recent_low_swings
        if len(highs) < 2 or len(lows) < 2:
            self._bias = "neutral"
            return

        hh = highs[-1].price > highs[-2].price
        hl = lows[-1].price > lows[-2].price
        lh = highs[-1].price < highs[-2].price
        ll = lows[-1].price < lows[-2].price

        if hh and hl:
            self._bias = "bullish"
        elif lh and ll:
            self._bias = "bearish"
        else:
            self._bias = "neutral"

    def bias(self) -> Bias:
        return self._bias
```

- [ ] **Step 4: Run bias tests to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_htf.py -q -k bias`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/strategy/htf.py tests/test_htf.py
git commit -m "feat: HTFBiasTracker — 4h swing structure to bias

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: HTFLevelFinder (target selection)

**Files:**
- Modify: `app/strategy/htf.py`
- Test: `tests/test_htf.py`

Target convention: for a long, the target is the nearest price ABOVE entry that
clears `min_r`; prefer a 4h bullish FVG's near (lower) edge, else a 30min swing
high. Mirror for shorts (nearest price BELOW entry; bearish FVG near/upper edge
or swing low). "Nearest qualifying" maximizes fill probability while meeting the
R floor. A 4h FVG that price has already traded back through (mitigated) is
ignored.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_htf.py`:

```python
def _tf_bar(i, o, h, l, c, tf):
    return Bar(
        instrument="MGC", timeframe=tf,
        ts=datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(hours=i),
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)), volume=100,
    )


def test_level_finder_returns_4h_fvg_above_entry_for_long():
    # Bullish FVG: bar1.high=20, bar3.low=24 → gap [20, 24], near edge 20.
    # No later bar trades back below 20 → unmitigated.
    bars = [
        _tf_bar(0, 18, 20, 17, 19, "4h"),   # b1 (high=20)
        _tf_bar(1, 19, 26, 19, 25, "4h"),   # b2 displacement up
        _tf_bar(2, 25, 27, 24, 26, "4h"),   # b3 (low=24) → gap 20..24
        _tf_bar(3, 26, 28, 25, 27, "4h"),   # stays above gap
    ]
    f = HTFLevelFinder(swing_lookback=2)
    f.rebuild(fvg_bars=bars, swing_bars=[])
    # entry=15, stop=13 → R=2, min_r=2 → need target >= 19. Near edge 20 qualifies.
    found = f.find_target("long", Decimal("15"), Decimal("13"), Decimal("2.0"))
    assert found is not None
    price, label = found
    assert price == Decimal("20")
    assert "4h FVG" in label


def test_level_finder_falls_back_to_30min_swing_when_no_qualifying_fvg():
    # 4h FVG near edge is only 1R away (too close); a 30min swing high qualifies.
    fvg_bars = [
        _tf_bar(0, 18, 20, 17, 19, "4h"),
        _tf_bar(1, 19, 23, 19, 22, "4h"),
        _tf_bar(2, 22, 24, 21, 23, "4h"),   # gap 20..21, near edge 20
        _tf_bar(3, 23, 25, 22, 24, "4h"),
    ]
    # entry=18.5, stop=17.5 → R=1, min_r=2 → need target >= 20.5; FVG edge 20 fails.
    # Build a 30min swing high at 22 (clears 20.5).
    swing_bars = _swing_sequence([(22, 19), (22, 19)])
    for b in swing_bars:
        object.__setattr__(b, "timeframe", "30min")
    f = HTFLevelFinder(swing_lookback=2)
    f.rebuild(fvg_bars=fvg_bars, swing_bars=swing_bars)
    found = f.find_target("long", Decimal("18.5"), Decimal("17.5"), Decimal("2.0"))
    assert found is not None
    price, label = found
    assert price >= Decimal("20.5")
    assert "30min swing" in label


def test_level_finder_returns_none_when_nothing_qualifies():
    f = HTFLevelFinder(swing_lookback=2)
    f.rebuild(fvg_bars=[], swing_bars=[])
    assert f.find_target("long", Decimal("15"), Decimal("13"), Decimal("2.0")) is None


def test_level_finder_ignores_mitigated_fvg():
    # Same bullish gap [20,24], but a later bar trades back to 19 (< 20) → mitigated.
    bars = [
        _tf_bar(0, 18, 20, 17, 19, "4h"),
        _tf_bar(1, 19, 26, 19, 25, "4h"),
        _tf_bar(2, 25, 27, 24, 26, "4h"),
        _tf_bar(3, 26, 27, 19, 20, "4h"),   # low=19 trades back into gap → mitigated
    ]
    f = HTFLevelFinder(swing_lookback=2)
    f.rebuild(fvg_bars=bars, swing_bars=[])
    assert f.find_target("long", Decimal("15"), Decimal("13"), Decimal("2.0")) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_htf.py -q -k level`
Expected: FAIL — `HTFLevelFinder` not defined.

- [ ] **Step 3: Implement `HTFLevelFinder`**

Append to `app/strategy/htf.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class _Gap:
    side: Literal["bullish", "bearish"]
    low: Decimal      # gap bottom
    high: Decimal     # gap top


class HTFLevelFinder:
    """4h FVGs (primary) + 30min swings (fallback) → a take-profit target.

    Precedence: nearest qualifying 4h FVG near-edge, else nearest qualifying
    30min swing, else None (caller falls back to VP / fixed R).
    """

    def __init__(self, swing_lookback: int = 3) -> None:
        self._swing_lookback = swing_lookback
        self._gaps: list[_Gap] = []           # unmitigated 4h gaps
        self._swing_highs: list[Decimal] = []
        self._swing_lows: list[Decimal] = []

    def rebuild(self, fvg_bars: list[Bar], swing_bars: list[Bar]) -> None:
        self._gaps = self._compute_unmitigated_gaps(fvg_bars)

        tracker = LiquidityTracker(LiquidityConfig(
            swing_lookback=self._swing_lookback,
            max_swings=50,
        ))
        for bar in swing_bars:
            tracker.on_bar(bar)
        self._swing_highs = [s.price for s in tracker.recent_high_swings]
        self._swing_lows = [s.price for s in tracker.recent_low_swings]

    @staticmethod
    def _compute_unmitigated_gaps(bars: list[Bar]) -> list[_Gap]:
        """Find 3-bar FVGs, then drop any a later bar has traded back through."""
        gaps: list[tuple[int, _Gap]] = []
        for i in range(2, len(bars)):
            b1, b3 = bars[i - 2], bars[i]
            if b3.low > b1.high:        # bullish gap
                gaps.append((i, _Gap("bullish", b1.high, b3.low)))
            elif b3.high < b1.low:      # bearish gap
                gaps.append((i, _Gap("bearish", b3.high, b1.low)))

        out: list[_Gap] = []
        for formed_idx, gap in gaps:
            mitigated = False
            for later in bars[formed_idx + 1:]:
                if gap.side == "bullish" and later.low <= gap.low:
                    mitigated = True
                    break
                if gap.side == "bearish" and later.high >= gap.high:
                    mitigated = True
                    break
            if not mitigated:
                out.append(gap)
        return out

    def find_target(
        self,
        side: str,
        entry: Decimal,
        stop: Decimal,
        min_r: Decimal,
    ) -> tuple[Decimal, str] | None:
        r = abs(entry - stop)
        if r <= 0:
            return None
        min_dist = r * min_r

        if side == "long":
            # 4h bullish FVG: target the near (lower) edge above entry.
            fvg_levels = [
                g.low for g in self._gaps
                if g.side == "bullish" and g.low > entry and (g.low - entry) >= min_dist
            ]
            if fvg_levels:
                price = min(fvg_levels)      # nearest above
                return price, f"HTF: 4h FVG @ {price} ({(price - entry) / r:.1f}R)"
            swing_levels = [
                p for p in self._swing_highs
                if p > entry and (p - entry) >= min_dist
            ]
            if swing_levels:
                price = min(swing_levels)
                return price, f"HTF: 30min swing @ {price} ({(price - entry) / r:.1f}R)"
            return None

        else:  # short
            fvg_levels = [
                g.high for g in self._gaps
                if g.side == "bearish" and g.high < entry and (entry - g.high) >= min_dist
            ]
            if fvg_levels:
                price = max(fvg_levels)      # nearest below
                return price, f"HTF: 4h FVG @ {price} ({(entry - price) / r:.1f}R)"
            swing_levels = [
                p for p in self._swing_lows
                if p < entry and (entry - p) >= min_dist
            ]
            if swing_levels:
                price = max(swing_levels)
                return price, f"HTF: 30min swing @ {price} ({(entry - price) / r:.1f}R)"
            return None
```

- [ ] **Step 4: Run all HTF tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_htf.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add app/strategy/htf.py tests/test_htf.py
git commit -m "feat: HTFLevelFinder — 4h FVG then 30min swing target selection

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: Config fields

**Files:**
- Modify: `app/bot_config.py:43` (after `vp_min_target_r`)
- Test: `tests/test_htf.py` (round-trip)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_htf.py`:

```python
def test_strategy_params_htf_defaults_inert():
    from app.bot_config import StrategyParams
    s = StrategyParams()
    assert s.htf_bias_enabled is False
    assert s.htf_bias_timeframe == "4h"
    assert s.htf_bias_lookback == 3
    assert s.htf_target_enabled is False
    assert s.htf_target_min_r == Decimal("2.0")
    assert s.htf_swing_timeframe == "30min"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_htf.py::test_strategy_params_htf_defaults_inert -q`
Expected: FAIL — `AttributeError: ... has no attribute 'htf_bias_enabled'`.

- [ ] **Step 3: Add the fields**

In `app/bot_config.py`, after line 43 (`vp_min_target_r: Decimal = Decimal("1.0")...`), inside `StrategyParams`, add:

```python

    # Higher-timeframe confluence (both default off → no behavior change)
    htf_bias_enabled: bool = False          # Part A: 4h swing-structure bias gate
    htf_bias_timeframe: str = "4h"          # timeframe for bias (NOTE: "4h" not "4hr")
    htf_bias_lookback: int = 3              # swing lookback on the bias timeframe
    htf_target_enabled: bool = False        # Part B: HTF target selection
    htf_target_min_r: Decimal = Decimal("2.0")  # min R an HTF level must deliver
    htf_swing_timeframe: str = "30min"      # fallback swing-target timeframe
```

- [ ] **Step 4: Run the test + full config tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_htf.py::test_strategy_params_htf_defaults_inert tests/test_config.py -q`
Expected: PASS (if `tests/test_config.py` does not exist, run only the first; it must PASS).

- [ ] **Step 5: Commit**

```bash
git add app/bot_config.py tests/test_htf.py
git commit -m "feat: HTF confluence config fields (default off)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: Engine integration (bias gate, VP bypass, target precedence)

**Files:**
- Modify: `app/execution/engine.py` — imports, `__init__`, `on_bar`
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write the failing integration tests**

Append to `tests/test_engine.py` (reuse existing helpers `make_runner`, `bar`, `in_ny_am`, `SHORT_SIGNAL_BARS`, `fifty_k_combine` — match the file's existing imports/fixtures; if a helper name differs, adapt to the local equivalent). Add a tiny stub bias/level object:

```python
class _StubBias:
    def __init__(self, value): self._v = value
    def bias(self): return self._v


class _StubLevels:
    def __init__(self, result): self._r = result
    def find_target(self, side, entry, stop, min_r): return self._r


async def _run_short_signal(engine, broker):
    captured = []
    async def cap(_, out): captured.append(out)
    engine.on_signal = cap
    for i, (o, h, l, c) in enumerate(SHORT_SIGNAL_BARS):
        await broker.inject_bar(bar(in_ny_am(i), o, h, l, c))
        await asyncio.sleep(0)
    return captured


async def test_htf_bias_blocks_counter_trend_short():
    from app.bot_config import StrategyParams
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    runner = make_runner()
    cfg = StrategyParams(vp_enabled=False, htf_bias_enabled=True)
    engine = ExecutionEngine(broker, state, [runner], replay_mode=True, strategy_cfg=cfg)
    engine.htf_bias = _StubBias("bullish")   # bullish bias → block shorts
    await broker.connect(); await engine.start()
    captured = await _run_short_signal(engine, broker)
    assert len(captured) == 1
    assert captured[0].placed is False
    assert captured[0].reason == "htf_bias"


async def test_htf_bias_agreement_bypasses_vp_filter():
    """Regression for 2026-05-27: bearish 4h bias lets a below-value short through."""
    from app.bot_config import StrategyParams
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    runner = make_runner()
    # VP enabled with a prior profile that would reject the short (entry below VAL).
    # Use the same VP-warm helper the other VP engine tests use; if none exists,
    # set runner.vp._prior to a profile whose val is far ABOVE the signal entry.
    cfg = StrategyParams(vp_enabled=True, htf_bias_enabled=True)
    engine = ExecutionEngine(broker, state, [runner], replay_mode=True, strategy_cfg=cfg)
    engine.htf_bias = _StubBias("bearish")    # agrees with short → bypass VP filter
    await broker.connect(); await engine.start()
    captured = await _run_short_signal(engine, broker)
    assert len(captured) == 1
    # Bypass means it is NOT denied for vp_filter; it should be placed (or denied
    # only for a non-VP reason like risk). Assert it was not a vp_filter denial.
    assert captured[0].reason != "vp_filter"


async def test_htf_target_overrides_vp_target():
    from app.bot_config import StrategyParams
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    runner = make_runner()
    cfg = StrategyParams(vp_enabled=False, htf_target_enabled=True)
    engine = ExecutionEngine(broker, state, [runner], replay_mode=True, strategy_cfg=cfg)
    engine.htf_levels = _StubLevels((Decimal("4400.0"), "HTF: 4h FVG @ 4400.0 (3.0R)"))
    placed = []
    async def cap(sig, out):
        if out.placed: placed.append(sig)
    engine.on_signal = cap
    for i, (o, h, l, c) in enumerate(SHORT_SIGNAL_BARS):
        await broker.inject_bar(bar(in_ny_am(i), o, h, l, c))
        await asyncio.sleep(0)
    assert placed and placed[0].target == Decimal("4400.0")


async def test_htf_disabled_is_unchanged():
    """Both flags off → no htf attributes consulted, behavior identical."""
    from app.bot_config import StrategyParams
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    runner = make_runner()
    cfg = StrategyParams(vp_enabled=False)   # htf flags default False
    engine = ExecutionEngine(broker, state, [runner], replay_mode=True, strategy_cfg=cfg)
    engine.htf_bias = _StubBias("bullish")   # present but must be ignored
    captured = await _run_short_signal(engine, broker)
    assert len(captured) == 1
    assert captured[0].reason != "htf_bias"   # gate not consulted when disabled
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_engine.py -q -k htf`
Expected: FAIL — `ExecutionEngine` has no attribute `htf_bias` / reasons don't match.

- [ ] **Step 3: Add engine HTF attributes**

In `app/execution/engine.py`, ensure `replace` is importable. At the top, find the dataclasses import and make it:

```python
import dataclasses
```

(If the file already does `from dataclasses import dataclass, field`, ADD a separate `import dataclasses` line so `dataclasses.replace` is available without disturbing existing names.)

In `ExecutionEngine.__init__`, after `self._started = False` (line ~227), add:

```python
        # HTF confluence trackers — set externally by main.py after warm-up.
        # Engine-owned (not per-runner) so they survive /api/strategy/reload.
        # None until wired; the on_bar gates no-op while None or while the
        # corresponding strategy_cfg flag is off.
        self.htf_bias = None      # HTFBiasTracker | None
        self.htf_levels = None    # HTFLevelFinder | None
```

- [ ] **Step 4: Replace the VP gate block in `on_bar` with HTF-aware pipeline**

In `app/execution/engine.py`, replace the existing VP gate block (lines ~321-338, the `# VP gate: filter + target override.` block ending with `signal = filtered`) with:

```python
        cfg = self.strategy_cfg
        vp_active = (
            runner.vp is not None
            and cfg is not None
            and cfg.vp_enabled
            and runner.vp.has_prior_profile()
        )

        # Part A — HTF bias gate. Block counter-trend signals; remember when
        # the bias AGREES (used to bypass the VP value-area filter below).
        bias_agrees = False
        if cfg is not None and cfg.htf_bias_enabled and self.htf_bias is not None:
            b = self.htf_bias.bias()
            if (b == "bullish" and signal.side == "short") or (
                b == "bearish" and signal.side == "long"
            ):
                log.info(
                    "HTF bias gate: %s signal blocked (4h bias=%s) | %s",
                    signal.side, b, signal.rationale,
                )
                htf_denied = OrderOutcome(placed=False, reason="htf_bias")
                if self.on_signal is not None:
                    try:
                        await self.on_signal(signal, htf_denied)
                    except Exception:
                        log.exception("on_signal callback raised (htf_bias)")
                return
            bias_agrees = (b == "bullish" and signal.side == "long") or (
                b == "bearish" and signal.side == "short"
            )

        # VP value-area filter — skipped when an enabled, agreeing 4h bias
        # vouches for the direction (the 2026-05-27 below-value-area shorts).
        if vp_active and not bias_agrees:
            if not runner.vp.passes_filter(signal, self.strategy_cfg):
                log.info(
                    "VP filter: rejected %s entry=%.2f outside value area | %s",
                    signal.side, float(signal.entry), signal.rationale,
                )
                vp_denied = OrderOutcome(placed=False, reason="vp_filter")
                if self.on_signal is not None:
                    try:
                        await self.on_signal(signal, vp_denied)
                    except Exception:
                        log.exception("on_signal callback raised (vp_filter)")
                return

        # Part B — target precedence: HTF (4h FVG → 30min swing) wins,
        # VP target is the fallback, fixed r_multiple is the final fallback.
        target_chosen = False
        if cfg is not None and cfg.htf_target_enabled and self.htf_levels is not None:
            found = self.htf_levels.find_target(
                signal.side, signal.entry, signal.stop, cfg.htf_target_min_r,
            )
            if found is not None:
                price, label = found
                signal = dataclasses.replace(
                    signal, target=price, rationale=signal.rationale + f" | {label}",
                )
                target_chosen = True
        if not target_chosen and vp_active:
            vp_signal = runner.vp.select_target(signal, self.strategy_cfg)
            if vp_signal is not None:
                signal = vp_signal
```

- [ ] **Step 5: Run HTF engine tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_engine.py -q -k htf`
Expected: PASS (4 tests). If `test_htf_bias_agreement_bypasses_vp_filter` needs a real VP prior profile, set `runner.vp._prior` to a `VolumeProfile` whose `val`/`vah` are well above the short entry (mirror how existing VP engine tests construct a prior profile).

- [ ] **Step 6: Run the FULL engine + VP suite for regressions**

Run: `.venv/Scripts/python.exe -m pytest tests/test_engine.py tests/test_volume_profile.py -q`
Expected: PASS for all tests that passed before this task (the pre-existing `test_signal_denied_when_already_at_max_contracts` reversal-reason failure noted in repo history is unrelated; do not let this task introduce NEW failures).

- [ ] **Step 7: Commit**

```bash
git add app/execution/engine.py tests/test_engine.py
git commit -m "feat: HTF bias gate, VP filter bypass, and HTF target precedence

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: main.py wiring — warm-up + refresh loop

**Files:**
- Modify: `app/main.py` — add `_warm_up_htf`, `_htf_refresh_loop`; attach trackers; manage task lifecycle in `_run_live`.

This task has no unit test (it is I/O orchestration); verification is by import +
a dry construction check, then live behavior. Keep the loop crash-proof (Rule 12).

- [ ] **Step 1: Add imports and helpers**

In `app/main.py`, near the other strategy imports (the `from app.strategy...` block around line 68-72), add:

```python
from app.strategy.htf import HTFBiasTracker, HTFLevelFinder
```

Add a module-level constant near the top-level config constants (after `_CT = ZoneInfo(...)`, line ~132):

```python
HTF_REFRESH_SECONDS = 60  # 4h/30min structure barely moves intraday; 60s is ample
```

Add these two functions next to `_warm_up_vp` (after it ends, ~line 660):

```python
async def _build_htf_trackers(
    broker: "Broker", s: "StrategyParams",
) -> tuple[HTFBiasTracker | None, HTFLevelFinder | None]:
    """Construct + warm HTF trackers from REST history. Returns (bias, levels).

    Either may be None if the feature is disabled. On fetch failure the tracker
    is returned empty (bias → neutral, find_target → None): fail-open.
    """
    from app.sim.topstepx import TopstepXBroker
    if not isinstance(broker, TopstepXBroker):
        return None, None

    bias_tracker: HTFBiasTracker | None = None
    level_finder: HTFLevelFinder | None = None

    if s.htf_bias_enabled:
        bias_tracker = HTFBiasTracker(lookback=s.htf_bias_lookback)
    if s.htf_target_enabled:
        level_finder = HTFLevelFinder(swing_lookback=s.htf_bias_lookback)

    if bias_tracker is None and level_finder is None:
        return None, None

    await _refresh_htf_once(broker, s, bias_tracker, level_finder)
    return bias_tracker, level_finder


async def _refresh_htf_once(
    broker: "Broker", s: "StrategyParams",
    bias_tracker: HTFBiasTracker | None, level_finder: HTFLevelFinder | None,
) -> None:
    """Fetch recent 4h + 30min bars and rebuild whichever trackers exist."""
    try:
        if bias_tracker is not None or level_finder is not None:
            bias_bars = await broker.get_historical_bars(
                timeframe=s.htf_bias_timeframe, days=30, limit=500,
            )
            if bias_tracker is not None:
                bias_tracker.rebuild(bias_bars)
            if level_finder is not None:
                swing_bars = await broker.get_historical_bars(
                    timeframe=s.htf_swing_timeframe, days=10, limit=500,
                )
                level_finder.rebuild(fvg_bars=bias_bars, swing_bars=swing_bars)
    except Exception:
        log.exception("HTF refresh failed — retaining last-known state")


async def _htf_refresh_loop(
    broker: "Broker", engine: "ExecutionEngine", s: "StrategyParams",
    shutdown: "asyncio.Event",
) -> None:
    """Background task: periodically rebuild the engine's HTF trackers."""
    while not shutdown.is_set():
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=HTF_REFRESH_SECONDS)
            break  # shutdown fired
        except asyncio.TimeoutError:
            pass
        await _refresh_htf_once(broker, s, engine.htf_bias, engine.htf_levels)
```

- [ ] **Step 2: Wire trackers in `_run_live` and start the loop**

In `app/main.py` `_run_live` (around line 550-560), after the VP warm-up block (`if runner is not None and bot_cfg is not None and runner.vp is not None: await _warm_up_vp(...)`), add:

```python
    htf_task = None
    if bot_cfg is not None and runner is not None:
        s = bot_cfg.strategy
        if s.htf_bias_enabled or s.htf_target_enabled:
            bias_tracker, level_finder = await _build_htf_trackers(broker, s)
            engine.htf_bias = bias_tracker
            engine.htf_levels = level_finder
            log.info(
                "HTF confluence active: bias=%s target=%s (bias_tf=%s swing_tf=%s)",
                s.htf_bias_enabled, s.htf_target_enabled,
                s.htf_bias_timeframe, s.htf_swing_timeframe,
            )
            htf_task = asyncio.create_task(
                _htf_refresh_loop(broker, engine, s, shutdown)
            )
```

NOTE: `_run_live` must have access to `engine` and `shutdown`. Confirm the
function signature provides them; the engine is created in `_async_main` and
passed in. If `_run_live` does not currently receive `engine`, thread it through
the call site (`await _run_live(broker, cfg, shutdown, runner=runner, bot_cfg=bot_cfg, engine=engine)`)
and add `engine` to the `_run_live` signature. Check lines ~549 and ~916.

- [ ] **Step 3: Cancel the loop on shutdown**

In `_run_live`, where the function tears down after `shutdown` is set (end of the function), add cancellation alongside the existing cleanup:

```python
    if htf_task is not None:
        htf_task.cancel()
        try:
            await htf_task
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 4: Import-and-construct smoke check**

Run:
```bash
.venv/Scripts/python.exe -c "import app.main; from app.strategy.htf import HTFBiasTracker, HTFLevelFinder; print('import OK')"
```
Expected: `import OK` with no traceback.

- [ ] **Step 5: Full test suite (no new failures)**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_backtest.py --ignore=tests/test_analytics_loader.py --ignore=tests/test_analytics_tools.py`
Expected: No NEW failures vs. the pre-existing baseline (the known-unrelated failures: `test_main.py::test_paper_mode_full_run`, the two `test_reconciler` balance-drift tests, `test_engine` max-contracts reversal, `test_api` backpressure — these predate this work; everything else PASS).

- [ ] **Step 6: Commit**

```bash
git add app/main.py
git commit -m "feat: wire HTF trackers — startup warm-up + 60s refresh loop

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: Frontend config exposure

**Files:**
- Modify: `frontend/src/types.ts:18-23` (strategy block)
- Modify: `frontend/src/components/ConfigPanel.tsx:153-157` (field rows) and the form serialization block (~229-234)

- [ ] **Step 1: Add fields to the TS type**

In `frontend/src/types.ts`, in the strategy interface (near `vp_min_target_r: string`, line ~23), add:

```typescript
  htf_bias_enabled: boolean
  htf_bias_timeframe: string
  htf_bias_lookback: number
  htf_target_enabled: boolean
  htf_target_min_r: string
  htf_swing_timeframe: string
```

- [ ] **Step 2: Add ConfigPanel field rows**

In `frontend/src/components/ConfigPanel.tsx`, after the `vp_min_target_r` row (ends line ~157, before the closing `]`), add:

```typescript
  {
    key: 'htf_bias_enabled', label: 'HTF Bias Filter', type: 'select', section: 'strategy',
    options: ['true', 'false'],
    hint: 'Block signals that fight the 4h swing-structure bias. Bullish 4h blocks shorts; bearish blocks longs. When the bias agrees, the VP value-area filter is bypassed.',
  },
  {
    key: 'htf_target_enabled', label: 'HTF Targets', type: 'select', section: 'strategy',
    options: ['true', 'false'],
    hint: 'Use a 4h FVG (then nearest 30min swing) as the take-profit target instead of the fixed R-multiple. Falls back to VP, then r_multiple.',
  },
  {
    key: 'htf_target_min_r', label: 'HTF Min Target R', type: 'slider', section: 'strategy',
    min: 1.0, max: 5.0, step: 0.1,
    hint: 'An HTF level must deliver at least this many R to be used as target. Below this, falls back to VP / fixed R.',
  },
```

(The `htf_bias_timeframe`, `htf_bias_lookback`, `htf_swing_timeframe` fields keep
their config defaults and are not surfaced as editable rows — they are advanced
and rarely changed; editing `bot_config.json` covers them.)

- [ ] **Step 3: Add form serialization**

In `ConfigPanel.tsx`, in the object that builds the strategy payload (near `vp_min_target_r: form.vp_min_target_r || '1.0',` line ~234), add:

```typescript
      htf_bias_enabled:   form.htf_bias_enabled !== 'false' ? (form.htf_bias_enabled === 'true') : false,
      htf_target_enabled: form.htf_target_enabled === 'true',
      htf_target_min_r:   form.htf_target_min_r || '2.0',
```

NOTE: match the EXACT boolean-coercion idiom used by the neighboring `vp_enabled`
line (`form.vp_enabled !== 'false'`). For HTF, default is OFF, so coerce to true
ONLY when explicitly `'true'`: use `form.htf_bias_enabled === 'true'` and
`form.htf_target_enabled === 'true'`. Preserve `htf_bias_timeframe`,
`htf_bias_lookback`, `htf_swing_timeframe` from the loaded config object so they
round-trip even though they have no editable row (spread the existing strategy
object or carry them explicitly, following how other non-row fields are handled).

- [ ] **Step 4: Type-check / build the frontend**

Run: `cd frontend && npm run build`
Expected: Clean build, no TypeScript errors. (CLAUDE.md Rule 4: frontend success = `npm run build` clean.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/ConfigPanel.tsx
git commit -m "feat: expose HTF confluence toggles in config panel

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Final verification (after all tasks)

- [ ] **Backend suite, no new failures:**
  `.venv/Scripts/python.exe -m pytest tests/test_htf.py tests/test_engine.py tests/test_volume_profile.py -q` → PASS.
- [ ] **Frontend builds clean:** `cd frontend && npm run build`.
- [ ] **Config round-trips:** start the bot (or paper mode), `GET /api/config` shows the six `htf_*` fields under `strategy`; `PATCH` with `htf_bias_enabled: true` persists to `bot_config.json` and `_engine.strategy_cfg` reflects it (hot-apply, no restart).
- [ ] **Manual live sanity (next session):** enable `htf_bias_enabled`, confirm the startup log line `HTF confluence active: ...`, and confirm a counter-trend signal logs `HTF bias gate: ... blocked`.

---

## Self-review notes (verified during planning)

- **Spec coverage:** Part A bias gate → Task 5; bias-bypass-VP regression → Task 5 Step 1 `test_htf_bias_agreement_bypasses_vp_filter`; Part B precedence (4h FVG → 30min → VP → fixed R) → Task 3 + Task 5; REST warm-up + refresh → Task 6; config lifecycle (persist/GET/PATCH auto via model_dump; frontend) → Tasks 4 + 7; fail-open → Task 6 `_refresh_htf_once` try/except and empty-tracker neutral/None.
- **Timeframe strings:** `"4h"` / `"30min"` used throughout; the `"4hr"` trap is called out in Task 4 and the background section.
- **Type consistency:** `bias()` returns `Bias` literal; `find_target(side, entry, stop, min_r)` signature identical in Task 3 (definition), Task 5 (engine call), and the `_StubLevels` test double. `rebuild(fvg_bars=, swing_bars=)` keyword form consistent. `passes_filter` / `select_target` names match between Task 1 (definition) and Task 5 (calls).
- **No behavior change when off:** Task 5 Step 1 `test_htf_disabled_is_unchanged` guards it; the VP path with flags off reduces to filter-then-target, identical to the old `apply()`.
