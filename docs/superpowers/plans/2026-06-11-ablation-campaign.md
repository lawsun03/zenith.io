# Ablation Test Campaign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the 5 structural ablations from `c:\Users\Lawrence\Downloads\ABLATION_TEST_SPEC.md` (T1 long-only, T2 4H-bias, T3 NY-only, T4 trail-1R exit, T5 drop-iFVG) against the frontier monthly-Combine config, reporting pass rate / PF / maxDD / long-short split on both the 2025–26 test 17mo and 2024 12mo periods.

**Architecture:** All runs go through `scripts/run_monthly_combine.py` (existing harness). T2/T3 are pure CLI. T1/T5 add a mode field to `StrategyParams` + `ComposerConfig` gated in the composer. T4 adds a trail mode to `PaperBroker`. A reporting upgrade (per-side PF, per-run PF/maxDD) lands first so every run including control reports the spec's table.

**Tech Stack:** Python 3 / pytest / existing backtest stack (PaperBroker, ExecutionEngine, StrategyRunner).

---

## Fixed context (verified against the repo 2026-06-11)

- **Control (frontier) config** = `bot_config.json` as-is (MNQ overrides: stop_buffer 3.0, min_absolute_body 5.0, r_multiple 3.5; swing_stop_lookback 30; partials 1.5R; killzones "all"; flatten ON in engine) **plus `--risk-pct 1.25`** on the CLI. Expected control results (round-5 regression anchors): **6/17 test, 4/12 2024**.
- **Bars:** `bars/bars_MNQ_test_2025_2026.csv` (17mo) and `bars/bars_MNQ_train_2024.csv` (12mo).
- **Slippage model is already ON** in every backtest: `PaperBroker(slippage_ticks_market=1)` slips entries and stop exits adversely by 1 tick. T4's requirement is satisfied by default — do not change it.
- **Command template** (every run is this, twice — once per bars file):
  ```
  python scripts/run_monthly_combine.py --bars <bars.csv> --instrument MNQ --timeframe 5min --risk-pct 1.25 [variant flags] --save-id <id> --save-label "<label>"
  ```

## Spec deviations (surfaced per Rule 7 — decided, not blended)

1. **T1 `allowed_sides` is a string** (`"both" | "long" | "short"`), not a list. The harness `--set k=v` override types values via `type(current)(v)`; `list("long")` would explode into characters. A string mode field keeps `--set allowed_sides=long` working.
2. **T2 is inverted.** `htf_bias_enabled` is already `false` in the frontier config — "bias OFF" *is* the control. The informative arm is bias **ON** (`--set htf_bias_enabled=True`). The backtest runner builds `HTFBiasTracker` when enabled and the engine gate passes during warmup (`bias() is None` blocks nothing), so this is runnable with zero code.
3. **T3 prior evidence is negative**: round-1 (pre-flatten, 0.75% r2.5) scored ny_am+ny_pm **0/17**. The spec's "check the per-killzone stats table first" is not possible — control runs killzones "all", so all fills are tagged with the single "All" zone. The run itself is the answer; treat it as likely-confirmatory-negative.
4. **T4 is backtest-only** (PaperBroker + BacktestConfig + harness flag). No live/BotConfig/UI wiring (CLAUDE.md Rule 13) until/if T4 wins — it's a research mode, not a deployed strategy feature. Flag this in the final report.
5. **T5 entry price = the confirmation bar's close** (bar 3 — the bar on which the signal can first act, and the price PaperBroker fills at). "Displacement close" (bar 2) would be lookahead: the event only fires after bar 3 closes.
6. **`--set htf_bias_enabled=True` works; `=False` would not** (`bool("False") is True`). Only the True arm is needed here.

## Decision rules (from spec — apply mechanically)

- **Stop rule:** a variant losing on BOTH pass rate and PF/maxDD vs control is dropped. No parameter rescue.
- T1 success: pass rate ≥ control AND PF/maxDD improve. If pass drops but PF jumps: report both, keep for the second-engine context.
- T2: report standalone; if T1 won, also run T2-on-T1.
- T3 success: pass rate ≥ control with fewer trades.
- T5 success: pass-rate gain OR drought-month trade count up with PF ≥ 1.05.
- Stacking: pairwise winners only, **max 3 combo runs** (each = 2 commands, test + 2024).
- Every run reports the 2024 year (regime honesty).

---

### Task 1: Harness reporting — per-side PF, per-run PF/maxDD

The spec's report table needs: monthly pass rate, trades/mo, PF, maxDD, MLL touches, long/short PF split. The harness already reports pass rate, trades, MLL fails. Add the rest.

**Files:**
- Modify: `app/backtest/runner.py` (`BacktestStats` ~line 70, `_compute_stats` ~line 200)
- Modify: `scripts/run_monthly_combine.py` (`run_month` ~line 145, `main` table/summary ~line 230, `_save_ui_result` ~line 266)

- [ ] **Step 1: Add `by_side` to `BacktestStats` and compute it in `_compute_stats`**

In `app/backtest/runner.py`, append a defaulted field to `BacktestStats`:

```python
    by_killzone: dict[str, dict]
    by_side: dict[str, dict] = field(default_factory=dict)
```

In `_compute_stats`, after the `by_killzone` block (after line ~246), add:

```python
    # Per-side breakdown: an exit fill's side is the opposite of the trade's.
    # Counts are exit fills (partials count separately); PF is unaffected.
    side_exits: dict[str, list[Decimal]] = {}
    for f in exits:
        trade_side = "long" if f["side"] == "short" else "short"
        side_exits.setdefault(trade_side, []).append(Decimal(f["realized_pnl_delta"]))
    by_side: dict[str, dict] = {}
    for sd, side_pnls in side_exits.items():
        gw = sum((p for p in side_pnls if p > 0), Decimal("0"))
        gl = abs(sum((p for p in side_pnls if p < 0), Decimal("0")))
        by_side[sd] = {
            "exits": len(side_pnls),
            "gross_win": float(gw),
            "gross_loss": float(gl),
            "net_pnl": float(gw - gl),
            "profit_factor": float(gw / gl) if gl > 0 else None,
        }
```

and pass `by_side=by_side` in the `BacktestStats(...)` constructor call.

- [ ] **Step 2: Return the new stats from `run_month`**

In `scripts/run_monthly_combine.py::run_month`, extend the returned dict:

```python
    return {
        "label": label,
        "trades": s.trades,
        "win_rate": s.win_rate,
        "net": s.net_pnl,
        "pf": s.profit_factor,
        "max_dd": s.max_drawdown,
        "gross_win": s.gross_win,
        "gross_loss": s.gross_loss,
        "by_side": s.by_side,
        **ev,
        "mll_breached": s.mll_breached,
    }
```

- [ ] **Step 3: Print per-month PF column and a run-level summary**

In `main()`: add `pf` to the header and row prints:

```python
    print(f"{'month':8s} {'trades':>6s} {'win%':>5s} {'net':>10s} {'pf':>5s} "
          f"{'min_eq':>9s} {'best_day':>9s} {'result':18s}")
```

row print (`pf` may be None):

```python
        pf_s = f"{r['pf']:.2f}" if r["pf"] is not None else "-"
        print(f"{r['label']:8s} {r['trades']:6d} {r['win_rate']:5.1f} "
              f"{r['net']:10.2f} {pf_s:>5s} {r['min_eq']:9.2f} {r['best_day']:9.2f} "
              f"{outcome:18s}")
```

Accumulate across months (init before the loop, update inside it):

```python
    gw_total = gl_total = Decimal("0")
    worst_dd = Decimal("0")
    side_acc: dict[str, dict] = {}
    ...
        gw_total += r["gross_win"]
        gl_total += r["gross_loss"]
        if r["max_dd"] > worst_dd:
            worst_dd = r["max_dd"]
        for sd, d in r["by_side"].items():
            acc = side_acc.setdefault(sd, {"exits": 0, "gw": 0.0, "gl": 0.0})
            acc["exits"] += d["exits"]
            acc["gw"] += d["gross_win"]
            acc["gl"] += d["gross_loss"]
```

After the months-summary print, add:

```python
    run_pf = float(gw_total / gl_total) if gl_total > 0 else None
    print(f"run PF: {run_pf:.2f} | worst-month maxDD: {worst_dd:.0f}"
          if run_pf is not None else f"run PF: - | worst-month maxDD: {worst_dd:.0f}")
    for sd in ("long", "short"):
        acc = side_acc.get(sd)
        if acc:
            spf = acc["gw"] / acc["gl"] if acc["gl"] > 0 else float("inf")
            print(f"  {sd}s: {acc['exits']} exits, net {acc['gw']-acc['gl']:+.0f}, PF {spf:.2f}")
```

Also add `"pf": r["pf"], "max_dd": str(r["max_dd"])` to the `rows.append({...})` dict.

- [ ] **Step 4: Persist run aggregates in the UI JSON**

In `_save_ui_result`, the function signature gains the aggregates — change the call site to
`_save_ui_result(args, rows, passed, failed, neither, run_pf, worst_dd, side_acc)` and the signature to match. Inside, set `"profit_factor": run_pf` (replacing `None`) in `stats`, and inside the `"monthly_combine"` dict add:

```python
            "run_pf": run_pf,
            "worst_month_max_dd": str(worst_dd),
            "by_side": side_acc,
```

- [ ] **Step 5: Smoke-verify on one month**

Run (fast — single month of bars):

```
python scripts/run_monthly_combine.py --bars bars/bars_MNQ_train_2024.csv --instrument MNQ --timeframe 5min --risk-pct 1.25 --window 12
```

(`--window 12` collapses 2024 into one run.) Expected: table renders with `pf` column, summary prints `run PF`, `longs:`/`shorts:` lines. No exceptions.

- [ ] **Step 6: Run touched-area tests**

Run: `python -m pytest tests/test_backtest_runner.py tests/test_per_killzone_stats.py -q`
Expected: PASS (note: 11 unrelated pre-existing failures exist repo-wide; only these files must pass).

- [ ] **Step 7: Commit**

```bash
git add app/backtest/runner.py scripts/run_monthly_combine.py
git commit -m "feat: per-side PF + run-level PF/maxDD in monthly-Combine harness"
```

---

### Task 2: Control reruns (regression anchor + baseline metrics)

- [ ] **Step 1: Run control on both periods**

```
python scripts/run_monthly_combine.py --bars bars/bars_MNQ_test_2025_2026.csv --instrument MNQ --timeframe 5min --risk-pct 1.25 --save-id ab_control_test --save-label "AB control 1.25% r3.5 (test 17mo)"
python scripts/run_monthly_combine.py --bars bars/bars_MNQ_train_2024.csv --instrument MNQ --timeframe 5min --risk-pct 1.25 --save-id ab_control_2024 --save-label "AB control 1.25% r3.5 (2024)"
```

- [ ] **Step 2: Verify pass counts match round 5 exactly**

Expected: **6/17** (test) and **4/12** (2024). If they differ, the Task-1 reporting change altered behavior — STOP, debug before any ablation runs (reporting must be read-only).

- [ ] **Step 3: Record control row**

Note pass rates, trades/mo, run PF, worst-month maxDD, MLL fails, long/short PF — this is the comparison row for every test.

---

### Task 3: T1 code — `allowed_sides` gate

**Files:**
- Modify: `app/bot_config.py` (`StrategyParams`, after `swing_stop_lookback` ~line 85)
- Modify: `app/strategy/composer.py` (`ComposerConfig` ~line 128, `on_displacement` ~line 224)
- Modify: `app/backtest/runner.py` (`_build_runner` ComposerConfig ~line 170)
- Modify: `app/main.py` (`_build_runner` ComposerConfig ~line 131)
- Create: `tests/test_ablation_modes.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ablation_modes.py`:

```python
"""Tests for the ablation-campaign mode switches (T1 allowed_sides,
T4 trail_1r, T5 displacement_only). One test per mode's defining behavior."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.sim.events import Bar
from app.strategy.composer import ComposerConfig, SweepDisplacementComposer
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.strategy.liquidity import SweepEvent, Swing

ET = ZoneInfo("America/New_York")


def bar(ts, o, h, l, c, instrument="MNQ"):
    return Bar(instrument=instrument, timeframe="5min", ts=ts,
               open=Decimal(o), high=Decimal(h), low=Decimal(l),
               close=Decimal(c), volume=100)


def ny_am(i):
    return datetime(2026, 3, 4, 9, 30 + i, tzinfo=ET)


def _cfg(**kw):
    return ComposerConfig(instrument="MNQ", trend_ema_period=0, **kw)


def _short_setup(composer, with_fvg=True):
    """High-side sweep then bearish displacement -> SHORT candidate."""
    ts0 = ny_am(0)
    b_sweep = bar(ts0, "21000", "21010", "20995", "21005")
    sweep = SweepEvent(
        side="high",
        swept_swing=Swing(kind="high", price=Decimal("21008"),
                          bar_ts=ts0, confirmed_ts=ts0),
        pattern="B_one_bar",
        sweep_extreme=Decimal("21010"),
        completed_at=ts0,
        sweep_bar=b_sweep,
    )
    composer.on_sweep(b_sweep, sweep)
    composer.on_bar_close(b_sweep)
    ts1 = ny_am(1)
    b_disp = bar(ts1, "21005", "21006", "20980", "20982")
    fvg = FairValueGap(side="bearish", low=Decimal("20985"),
                       high=Decimal("21000"), created_at=ts1) if with_fvg else None
    event = DisplacementEvent(
        side="bearish", displacement_bar=b_disp, body_size=Decimal("23"),
        atr_at_event=Decimal("5"), body_to_atr=Decimal("4.6"), fvg=fvg,
    )
    return composer.on_displacement(b_disp, event), b_disp


class TestAllowedSides:
    def test_short_suppressed_when_long_only(self):
        signal, _ = _short_setup(SweepDisplacementComposer(_cfg(allowed_sides="long")))
        assert signal is None

    def test_short_fires_when_both(self):
        signal, _ = _short_setup(SweepDisplacementComposer(_cfg()))
        assert signal is not None
        assert signal.side == "short"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_ablation_modes.py -q`
Expected: FAIL — `TypeError: ComposerConfig.__init__() got an unexpected keyword argument 'allowed_sides'`

- [ ] **Step 3: Implement**

`app/bot_config.py`, in `StrategyParams` after `swing_stop_lookback`:

```python
    # Ablation T1: restrict signal side. "both" (default) | "long" | "short".
    # String (not list) so the monthly harness's --set k=v override can type it.
    allowed_sides: str = "both"
```

`app/strategy/composer.py`, in `ComposerConfig` after `swing_stop_lookback`:

```python
    # Side gate: "both" | "long" | "short". Non-matching signals suppressed
    # at emission (ablation T1 — long-only test).
    allowed_sides: str = "both"
```

`app/strategy/composer.py`, in `on_displacement`, directly after the `if event.fvg is None:` block:

```python
        if self.config.allowed_sides != "both":
            want_side = "long" if event.side == "bullish" else "short"
            if want_side != self.config.allowed_sides:
                log.info(
                    "Signal blocked: %s side disabled (allowed_sides=%s)",
                    want_side, self.config.allowed_sides,
                )
                return None
```

`app/backtest/runner.py` `_build_runner`, inside the faithful-path `ComposerConfig(...)` call, after `swing_stop_lookback=s.swing_stop_lookback,`:

```python
                swing_stop_lookback=s.swing_stop_lookback,
                allowed_sides=s.allowed_sides,
```

`app/main.py` `_build_runner`, same addition after `swing_stop_lookback=s.swing_stop_lookback,` (~line 141).

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_ablation_modes.py tests/test_strategy.py -q`
Expected: PASS (test_strategy.py guards the default-mode behavior didn't change).

- [ ] **Step 5: Commit**

```bash
git add app/bot_config.py app/strategy/composer.py app/backtest/runner.py app/main.py tests/test_ablation_modes.py
git commit -m "feat: allowed_sides composer gate (ablation T1, long-only)"
```

---

### Task 4: T1 runs — long-only

- [ ] **Step 1: Run both periods**

```
python scripts/run_monthly_combine.py --bars bars/bars_MNQ_test_2025_2026.csv --instrument MNQ --timeframe 5min --risk-pct 1.25 --set allowed_sides=long --save-id ab_t1_test --save-label "AB T1 long-only (test 17mo)"
python scripts/run_monthly_combine.py --bars bars/bars_MNQ_train_2024.csv --instrument MNQ --timeframe 5min --risk-pct 1.25 --set allowed_sides=long --save-id ab_t1_2024 --save-label "AB T1 long-only (2024)"
```

- [ ] **Step 2: Sanity-check the mechanism**

The run summary's `shorts:` line must be **absent** (zero short exits). If shorts appear, the gate isn't wired — stop and debug.

- [ ] **Step 3: Score vs control**

T1 success = pass rate ≥ control AND PF/maxDD improve. Expect trade count roughly halved; watch whether drought months deepen. Record both outcomes if split (pass rate down, PF up).

---

### Task 5: T2 runs — 4H bias ON (inverted vs spec; control is already bias-OFF)

- [ ] **Step 1: Run both periods**

```
python scripts/run_monthly_combine.py --bars bars/bars_MNQ_test_2025_2026.csv --instrument MNQ --timeframe 5min --risk-pct 1.25 --set htf_bias_enabled=True --save-id ab_t2_test --save-label "AB T2 4h-bias ON (test 17mo)"
python scripts/run_monthly_combine.py --bars bars/bars_MNQ_train_2024.csv --instrument MNQ --timeframe 5min --risk-pct 1.25 --set htf_bias_enabled=True --save-id ab_t2_2024 --save-label "AB T2 4h-bias ON (2024)"
```

- [ ] **Step 2: Sanity-check**

Trade counts should DROP vs control (the gate blocks counter-bias signals). If identical to control, the bias tracker isn't engaging — check the runner built `HTFBiasTracker` (it does when `htf_bias_enabled`), debug before scoring.

- [ ] **Step 3: Score vs control**

Bias-ON must BEAT control to matter (control = bias OFF already). Note the T1 interaction: if T1 won and bias mostly blocks counter-trend shorts, bias-ON adds nothing on top of long-only — run the T1+T2 stack only if both independently look positive.

---

### Task 6: T3 runs — NY sessions only

- [ ] **Step 1: Run both periods**

```
python scripts/run_monthly_combine.py --bars bars/bars_MNQ_test_2025_2026.csv --instrument MNQ --timeframe 5min --risk-pct 1.25 --killzones ny_am,ny_pm --save-id ab_t3_test --save-label "AB T3 NY-only (test 17mo)"
python scripts/run_monthly_combine.py --bars bars/bars_MNQ_train_2024.csv --instrument MNQ --timeframe 5min --risk-pct 1.25 --killzones ny_am,ny_pm --save-id ab_t3_2024 --save-label "AB T3 NY-only (2024)"
```

- [ ] **Step 2: Score vs control**

Success = pass rate ≥ control with fewer trades. Prior (round-1, different sizing/ruleset): ny-only was 0/17 — expect negative; the run makes it honest under the current ruleset.

---

### Task 7: Stack T1–T3 winners (config-only stacks)

- [ ] **Step 1: Decide stacks**

Apply the stop rule to T1/T2/T3. Pairwise-stack the winners — flags combine on one command line (e.g. T1+T3 = `--set allowed_sides=long --killzones ny_am,ny_pm`). **Max 3 combos**, each run on both periods, save-ids `ab_stack_<a><b>_test/_2024`, e.g. `ab_stack_t1t3_test`. If ≤1 winner, skip stacking entirely and say so.

- [ ] **Step 2: Run + score**

Same control comparison. Record the best stack — it becomes the second comparison row for T4/T5.

---

### Task 8: T4 code — `trail_1r` exit mode

**Files:**
- Modify: `app/sim/paper.py` (`_OpenBracket` ~line 49, `__init__` ~line 112, `place_bracket` ~line 260, `inject_bar` ~line 410)
- Modify: `app/backtest/runner.py` (`BacktestConfig` ~line 91, `run_backtest` PaperBroker ctor ~line 325)
- Modify: `scripts/run_monthly_combine.py` (argparse + `run_month`)
- Test: `tests/test_ablation_modes.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ablation_modes.py`:

```python
class TestTrail1R:
    def test_ratchets_at_exact_1r_and_exits_on_stop_only(self):
        from app.sim.paper import PaperBroker

        async def run():
            br = PaperBroker(starting_balance=Decimal("50000"),
                             slippage_ticks_market=0, trail_1r=True)
            fills = []

            async def collect(f):
                fills.append(f)

            br.on_fill(collect)
            await br.connect()
            ts = datetime(2026, 3, 4, 14, 30, tzinfo=timezone.utc)
            await br.inject_bar(bar(ts, "21000", "21000", "21000", "21000"))
            res = await br.place_bracket(
                "MNQ", "long", 2,
                entry=Decimal("21000"), stop=Decimal("20990"),
                target=Decimal("21035"),
            )
            assert res.success
            b = br._open[res.entry_order_id]
            assert b.trail_r == Decimal("10")
            assert b.partial_target is None  # no partials in trail mode

            # +1R high: stop ratchets to BE exactly (effective next bar)
            await br.inject_bar(bar(ts, "21000", "21010", "20995", "21008"))
            assert b.stop == Decimal("21000")

            # high crosses the old TP (21035): must NOT take profit; +3R
            # reached so stop ratchets to +2R
            await br.inject_bar(bar(ts, "21008", "21036", "21005", "21030"))
            assert res.entry_order_id in br._open
            assert b.stop == Decimal("21020")

            # low touches the ratcheted stop -> stop exit at the stop price
            await br.inject_bar(bar(ts, "21030", "21031", "21019", "21022"))
            assert res.entry_order_id not in br._open
            exit_fill = fills[-1]
            assert exit_fill.is_stop
            assert exit_fill.fill_price == Decimal("21020")

        asyncio.run(run())
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_ablation_modes.py::TestTrail1R -q`
Expected: FAIL — `TypeError: PaperBroker.__init__() got an unexpected keyword argument 'trail_1r'`

- [ ] **Step 3: Implement in PaperBroker**

`_OpenBracket` — add after `entry_time`:

```python
    # Trail-1R mode (ablation T4): R distance at entry; ratchet level reached.
    trail_r: Decimal | None = None
    trail_level: int = 0
```

`PaperBroker.__init__` — add parameter and store:

```python
        max_entry_slippage_frac: Decimal = Decimal("0"),
        trail_1r: bool = False,  # ablation T4: no TP, stop ratchets +1R per +1R MFE
    ) -> None:
        ...
        self._trail_1r = trail_1r
```

`place_bracket` — set `trail_r` on the new bracket and skip partials in trail mode. Replace the partial block:

```python
        if self._trail_1r:
            bracket.trail_r = abs(slipped_entry - stop)
        elif self._partial_profit_r > 0 and size >= 2:
            r = abs(slipped_entry - stop)
            ...existing partial code unchanged...
```

`inject_bar` — disable the target leg and ratchet on bar close. Change `target_hit`:

```python
            target_hit = bracket.trail_r is None and (
                (bracket.side == "long" and bar.high >= bracket.target)
                or (bracket.side == "short" and bar.low <= bracket.target)
            )
```

and replace the bare `continue` in the `else:` branch (no stop, no target) with:

```python
            else:
                # Trail ratchet applies on bar CLOSE — the raised stop is live
                # from the next bar. Raising intra-bar from this bar's high and
                # then stopping on this bar's low would assume the high printed
                # first (lookahead).
                if bracket.trail_r:
                    fav = (bar.high - bracket.entry if bracket.side == "long"
                           else bracket.entry - bar.low)
                    levels = int(fav / bracket.trail_r)
                    if levels > bracket.trail_level:
                        bracket.trail_level = levels
                        if bracket.side == "long":
                            new_stop = bracket.entry + bracket.trail_r * (levels - 1)
                            if new_stop > bracket.stop:
                                bracket.stop = new_stop
                        else:
                            new_stop = bracket.entry - bracket.trail_r * (levels - 1)
                            if new_stop < bracket.stop:
                                bracket.stop = new_stop
                        log.info("Trail ratchet: %s stop -> %s (level %d)",
                                 bracket.order_id, bracket.stop, levels)
                continue
```

(Note the partial-profit check earlier in `inject_bar` never fires in trail mode because `partial_target` stays None.)

- [ ] **Step 4: Wire through BacktestConfig and the harness**

`app/backtest/runner.py` `BacktestConfig` — add after `max_entry_slippage_frac`:

```python
    trail_1r: bool = False  # ablation T4: trailing 1R-ratchet exit, no TP, no partials
```

`run_backtest` PaperBroker ctor:

```python
        max_entry_slippage_frac=cfg.max_entry_slippage_frac,
        trail_1r=cfg.trail_1r,
```

`scripts/run_monthly_combine.py` — argparse:

```python
    ap.add_argument("--trail-1r", action="store_true",
                    help="Exit mode trail_1r: no TP, no partials, stop ratchets "
                         "+1R per +1R of favorable excursion (ablation T4)")
```

in `main()` after the partial_r default resolution:

```python
    if args.trail_1r:
        args.partial_r = "0"   # trail mode excludes partials by definition
```

and in `run_month`'s `BacktestConfig(...)`:

```python
        soft_buffer=Decimal(args.soft_buffer),
        trail_1r=args.trail_1r,
```

Also record it in `_save_ui_result`'s `monthly_combine.params`: `"trail_1r": args.trail_1r,`.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_ablation_modes.py tests/test_paper_broker_slippage.py tests/test_partial_exit.py tests/test_partial_profit.py -q`
Expected: PASS (default-mode paper-broker behavior unchanged).

- [ ] **Step 6: Commit**

```bash
git add app/sim/paper.py app/backtest/runner.py scripts/run_monthly_combine.py tests/test_ablation_modes.py
git commit -m "feat: trail_1r ratchet exit mode in PaperBroker (ablation T4)"
```

---

### Task 9: T4 runs — trailing 1R ratchet

- [ ] **Step 1: Run both periods**

```
python scripts/run_monthly_combine.py --bars bars/bars_MNQ_test_2025_2026.csv --instrument MNQ --timeframe 5min --risk-pct 1.25 --trail-1r --save-id ab_t4_test --save-label "AB T4 trail-1R (test 17mo)"
python scripts/run_monthly_combine.py --bars bars/bars_MNQ_train_2024.csv --instrument MNQ --timeframe 5min --risk-pct 1.25 --trail-1r --save-id ab_t4_2024 --save-label "AB T4 trail-1R (2024)"
```

- [ ] **Step 2: Spec-mandated checks**

- Slippage: ON by default (1 tick adverse on every stop exit — and ALL trail exits are stop exits). Confirm `slippage_ticks_market` was not touched.
- Consistency cap: check `best_day` column on passing months — occasional huge winners can trip the 50%-of-profit rule (`eval_month` already enforces it; just read the results).

- [ ] **Step 3: Score vs control AND vs best stack (if any)**

---

### Task 10: T5 code — `confirmation: "displacement_only"`

**Files:**
- Modify: `app/bot_config.py` (`StrategyParams`)
- Modify: `app/strategy/composer.py` (`ComposerConfig`, `on_displacement`, `_build_signal`)
- Modify: `app/backtest/runner.py` + `app/main.py` (`_build_runner` ComposerConfig)
- Test: `tests/test_ablation_modes.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ablation_modes.py`:

```python
class TestDisplacementOnly:
    def test_signal_fires_without_ifvg(self):
        composer = SweepDisplacementComposer(_cfg(confirmation="displacement_only"))
        signal, b_disp = _short_setup(composer, with_fvg=False)
        assert signal is not None
        assert signal.side == "short"
        assert signal.entry == b_disp.close          # confirmation-bar close
        assert signal.fvg_low is None and signal.fvg_high is None
        # stop unchanged: past sweep extreme (21010) + stop_buffer (0.30)
        assert signal.stop == Decimal("21010.30")

    def test_default_mode_still_requires_ifvg(self):
        signal, _ = _short_setup(SweepDisplacementComposer(_cfg()), with_fvg=False)
        assert signal is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_ablation_modes.py::TestDisplacementOnly -q`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'confirmation'`

- [ ] **Step 3: Implement**

`app/bot_config.py`, `StrategyParams` after `allowed_sides`:

```python
    # Ablation T5: signal confirmation chain. "ifvg" (default) = displacement
    # bar must invert a prior FVG, entry at the iFVG zone. "displacement_only"
    # = sweep + opposite displacement bar suffices; entry at the confirmation-
    # bar close (the first actionable price — bar2's close would be lookahead).
    confirmation: str = "ifvg"
```

`app/strategy/composer.py`, `ComposerConfig` after `allowed_sides`:

```python
    # "ifvg" | "displacement_only" — see StrategyParams.confirmation.
    confirmation: str = "ifvg"
```

`on_displacement`, change the first guard:

```python
        if event.fvg is None and self.config.confirmation != "displacement_only":
            return None  # no entry zone, no trade
```

`_build_signal`, replace from `fvg = event.fvg` through the `else:` branch's target line with:

```python
        cfg = self.config
        fvg = event.fvg
        displacement_only = cfg.confirmation == "displacement_only"

        lookback = cfg.swing_stop_lookback
        if event.side == "bullish":
            side: Side = "long"
            entry = bar.close if displacement_only else fvg.high
            if lookback > 0 and self._bar_lows:
                swing_anchor = min(self._bar_lows)
                stop_anchor = min(swing_anchor, awaiting.sweep.sweep_extreme)
            else:
                stop_anchor = awaiting.sweep.sweep_extreme
            stop = stop_anchor - cfg.stop_buffer
            r = entry - stop
            target = entry + r * cfg.r_multiple
        else:
            side = "short"
            entry = bar.close if displacement_only else fvg.low
            if lookback > 0 and self._bar_highs:
                swing_anchor = max(self._bar_highs)
                stop_anchor = max(swing_anchor, awaiting.sweep.sweep_extreme)
            else:
                stop_anchor = awaiting.sweep.sweep_extreme
            stop = stop_anchor + cfg.stop_buffer
            r = stop - entry
            target = entry - r * cfg.r_multiple
```

(the existing `assert fvg is not None` line is removed), and adjust the rationale + Signal fields:

```python
        fvg_desc = f"FVG {fvg.low}–{fvg.high}" if fvg is not None else "no-FVG (displacement-only)"
        rationale = (
            f"{awaiting.killzone_name}: "
            f"{awaiting.sweep.pattern} sweep of {awaiting.sweep.side} "
            f"@ {awaiting.sweep.swept_swing.price}, "
            f"{event.side} displacement "
            f"({event.body_to_atr:.2f}× ATR), "
            f"{fvg_desc}"
        )
        ...
            fvg_low=fvg.low if fvg is not None else None,
            fvg_high=fvg.high if fvg is not None else None,
```

Wiring: add `confirmation=s.confirmation,` to both `ComposerConfig(...)` calls (`app/backtest/runner.py` `_build_runner`, `app/main.py` `_build_runner`), next to `allowed_sides`.

Downstream None-safety (verified during planning, re-verify in test run):
- `SetupGrader._fvg_singular` guards `signal.fvg_low is None` → returns singular-pass.
- `StrategyRunner._arm_or_return` returns the signal directly when `fvg_low is None` (and frontier runs `ifvg_entry_mode="close"` anyway).
- `PaperBroker.place_bracket` fills at market regardless of nominal entry.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_ablation_modes.py tests/test_strategy.py tests/test_grader_score.py tests/test_ifvg_inversion.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/bot_config.py app/strategy/composer.py app/backtest/runner.py app/main.py tests/test_ablation_modes.py
git commit -m "feat: displacement_only confirmation mode (ablation T5, drop iFVG gate)"
```

---

### Task 11: T5 runs — displacement-only

- [ ] **Step 1: Run both periods**

```
python scripts/run_monthly_combine.py --bars bars/bars_MNQ_test_2025_2026.csv --instrument MNQ --timeframe 5min --risk-pct 1.25 --set confirmation=displacement_only --save-id ab_t5_test --save-label "AB T5 displacement-only (test 17mo)"
python scripts/run_monthly_combine.py --bars bars/bars_MNQ_train_2024.csv --instrument MNQ --timeframe 5min --risk-pct 1.25 --set confirmation=displacement_only --save-id ab_t5_2024 --save-label "AB T5 displacement-only (2024)"
```

- [ ] **Step 2: Sanity-check**

Trade count must INCREASE vs control (fewer filters). If it doesn't, the mode isn't engaging.

- [ ] **Step 3: Score vs control AND vs best stack**

Success = pass-rate gain OR drought-month (3–19 trades/mo in control) trade counts up with PF ≥ 1.05. Compare the drought months specifically (per-month table).

---

### Task 12: Final stacks, report, memory, commit

- [ ] **Step 1: T4/T5 vs best stack (only if both a T4-or-T5 winner AND a config-stack winner exist)**

Combine flags (e.g. `--set allowed_sides=long --trail-1r`). Counts against the max-3-combos budget from Task 7.

- [ ] **Step 2: Write the campaign report**

Create `trade_analysis/<today>_ablation_campaign.md` with one table, all runs as rows, columns exactly per spec: monthly pass rate (test + 2024), trades/mo, PF, maxDD (worst-month), MLL touches, long/short PF split, verdict per decision rule. Include the spec-deviation notes (T2 inversion, T3 prior, T4 backtest-only / Rule-13 deferral, T5 entry-bar choice) and the stop-rule outcomes.

- [ ] **Step 3: Update memory**

Update the auto-memory `project_monthly_combine_sim.md` (or add `project_ablation_campaign.md` + MEMORY.md line) with: winners/losers, whether the 35% plateau moved, and what's next. If T1 confirmed shorts-negative, note the freed-cushion implication for the ORB second engine ([[project_orb_second_engine]]).

- [ ] **Step 4: Commit the report**

```bash
git add trade_analysis/ docs/superpowers/plans/2026-06-11-ablation-campaign.md
git commit -m "docs: ablation campaign results (T1-T5 vs 35% plateau)"
```

---

## Self-review notes

- Spec coverage: T1→Tasks 3–4, T2→Task 5, T3→Task 6, stacking→Tasks 7/12, T4→Tasks 8–9, T5→Tasks 10–11, report table→Tasks 1+12, guardrails (2024 always, no new tunables — all new fields are mode switches, one unit test per mode) → embedded.
- Run-order matches spec: config-only first (T1 code is trivial), stacks, then T4/T5.
- Types consistent: `allowed_sides: str`, `confirmation: str`, `trail_1r: bool` used identically at definition, wiring, CLI, and tests.
