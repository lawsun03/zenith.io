# VWAP Mean-Reversion Engine — Solo Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a VWAP mean-reversion detector as a third candidate engine and benchmark it solo on MNQ through the monthly-Combine harness; score complementarity vs iFVG and ORB pass months.

**Architecture:** `app/strategy/vwap.py`: `VWAPDetector` (session-anchored VWAP + volume-weighted σ-bands; closed-bar fade entries; per-side episodic re-arm on VWAP retouch) and `VWAPRunner` (same duck-type shim as ORBRunner). `StrategyParams.engine="vwap"` + `vwap_anchor_et` / `vwap_band_sigma` / `vwap_stop_sigma` fields; selection in both `_build_runner` sites. No engine/PaperBroker changes.

**Tech Stack:** Python 3 / pytest / existing stack. Decimal math (`Decimal.sqrt()` for σ).

---

## Detector semantics (locked)

- Cumulative from session anchor: `sum_w=Σv`, `sum_p=Σ(tp·v)`, `sum_p2=Σ(tp²·v)` with `tp=(h+l+c)/3`; `vwap=sum_p/sum_w`; `σ=sqrt(max(sum_p2/sum_w − vwap², 0))`.
- Session = anchor time ET → next anchor (an 08:00 bar belongs to yesterday's 09:30 session; the 18:00 anchor naturally spans midnight). Reset sums at each new session; both sides armed; `min_bars=6` warmup before any signal.
- LONG when armed and `close < vwap − k·σ` (σ>0): entry=close, target=vwap (fixed at entry), stop=`entry − s·σ`. SHORT mirror. Side disarms on fire; re-arms when a later close touches/crosses VWAP (long side: `close ≥ vwap`; short side: `close ≤ vwap`).
- Signal fields: killzone="VWAP", sweep_pattern="VWAP", sweep_extreme=band level, fvg None, sweep_bar_range=σ.
- Entry cutoff/flatten enforced by the engine as usual.

### Task 1: Detector + runner (TDD)

**Files:** Create `app/strategy/vwap.py`, `tests/test_vwap.py`

- [ ] Tests (assert against the detector's exposed `vwap`/`sigma` properties, not hand-computed decimals): long fade fires below lower band with target==vwap and stop==entry−s·σ; no signal inside bands; no signal before min_bars; side disarms after fire and re-arms only after a close back at VWAP; session reset at anchor (vwap == first bar's typical price); σ==0 → no signal.
- [ ] Implement `VWAPConfig` (instrument, anchor_et="09:30", band_sigma=Decimal("2.5"), stop_sigma=Decimal("1.5"), min_bars=6), `VWAPDetector.on_bar(bar) -> Signal|None`, `VWAPRunner` (copy of the ORBRunner shim shape).
- [ ] `pytest tests/test_vwap.py -q` green; commit.

### Task 2: engine="vwap" selection

**Files:** `app/bot_config.py`, `app/backtest/runner.py`, `app/main.py`, `tests/test_vwap.py`

- [ ] StrategyParams: `engine` docstring gains "vwap"; add `vwap_anchor_et: str = "09:30"`, `vwap_band_sigma: Decimal = Decimal("2.5")`, `vwap_stop_sigma: Decimal = Decimal("1.5")`.
- [ ] Both `_build_runner` sites: `if s.engine == "vwap": return VWAPRunner(...)` (mirror of the orb branch).
- [ ] Selection test (mirror of ORB's); smoke: `--set engine=vwap --window 12` on 2024 MNQ → trades fire, no exceptions; commit.

### Task 3: 2024 MNQ sweep (12 configs)

- [ ] anchors {09:30, 18:00} × band {2.0, 2.5, 3.0} × stop {1.0, 1.5}; save-ids `vwap_<a>_b<k>_s<s>_2024`. Score by PF + pass rate (the MGC lesson: passes at PF≈1 are luck).

### Task 4: Test validation + complementarity + report

- [ ] Validate winning row (if any) on `bars_MNQ_test_2025_2026.csv`.
- [ ] Complementarity: per-month passes vs iFVG control AND ORB r2.5 (iFVG droughts: 2025-01/03/04/05/06/10/11, 2026-01/03/04; ORB-converted: 2025-04/10, 2026-04 — does VWAP convert any *remaining* drought: 2025-01/03/05/06/11, 2026-01/03?).
- [ ] Report `trade_analysis/2026-06-11_vwap_benchmark.md`, memory update, commit. Stop rule applies: no winning row on train → no validation, report the negative.
