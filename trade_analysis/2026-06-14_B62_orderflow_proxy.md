# B62 — Orderflow-proxy confirmation + veto for ORB (Phase 1) — REJECTED

**Session:** wk4-b62 (2026-06-14) · **Objective:** combine/funded (ORB) · **Verdict:** Phase 1 NO-GO — no engine built

## Hypothesis (Lawrence-requested)

We have no tick/L2 data, so approximate orderflow from OHLCV and test whether it
sharpens the NQ 5min ORB engine:
- **(a) CONFIRM:** take the ORB breakout only when a 3-bar cumulative-delta proxy
  **and** RVOL confirm the breakout direction.
- **(b) VETO/EXIT:** exit early on a 3-bar cum-delta divergence against the position.

Per the B62 spec, **Phase 1 is a cheap falsification first**: does the proxy at the
breakout correlate with ORB outcome at all? If there is no PF separation, do **not**
build the engine variants.

## Method (deterministic, fixed formulas — CLAUDE.md Rule 5)

- **Bars:** `bars/bars_MNQ_dbv_2021_2026.csv` (1-min) resampled to 5min, right-labelled
  at the close minute so the breakout bar aligns with the recorded `entry_ts`
  (verified: 2021-06-14 entry 14039.75 = 5min bar [13:45–13:49] close 14039.50 + 1 tick slippage).
- **Trades:** `research/mfe_mae_orb_clean.csv` — 1030 ORB trades, 5y excl 2022
  (baseline WR 0.450, PF 1.214, net +$49,952). Matches Lesson 88.
- **Cum-delta proxy** (per 5min bar): `delta = volume * (2·(close−low)/(high−low) − 1)`
  — CLV-weighted volume; close-at-high ⇒ +volume, close-at-low ⇒ −volume.
- **3-bar signed cum-delta:** `cd3_signed = dir · Σ delta` over breakout bar + 2 prior
  bars (`dir` = +1 long / −1 short); `cd3_ratio = cd3_signed / Σ volume ∈ [−1,+1]`
  (>0 confirms the breakout).
- **RVOL:** breakout-bar volume ÷ mean volume of the same ET time-of-day 5min bar over
  the prior 20 sessions (NaN if <20 priors; 20/1030 early-2021 trades dropped).
- **GO/NO-GO:** top-40% vs bottom-40% PF ratio ≥ 1.40× and year-consistent (B45/B49 convention).

Script: `scripts/analyze_b62_orderflow.py` (analysis only; no bot behavior changed).

## Results

| Proxy | bottom-40% PF | top-40% PF | top/bottom ratio | verdict |
|-------|--------------|-----------|------------------|---------|
| cd3_ratio (directional cum-delta) | 1.106 | 1.149 | **1.039** | NO-GO |
| RVOL (breakout vs prior-20 same-TOD) | 1.221 | 1.267 | **1.037** | NO-GO |
| Combined gate (cd3>0 AND rvol≥median) | gate-fail 1.195 | gate-pass 1.248 | **1.044** | NO-GO |

**Sign split:** CONFIRMED (cd3_ratio>0) = 93% of trades, PF 1.204; NOT-CONFIRMED
(cd3_ratio≤0) = 7%, PF **1.361** — the un-confirmed minority is *better*, the opposite
of the hypothesis. A confirm gate would remove a slightly stronger cohort.

**No year consistency:** cd3 top beats bottom only in 2021/2026; bottom wins in
2024/2025. RVOL top wins in 2024/2025 but bottom crushes top in 2021 (2.12 vs 1.07).

## Mechanism — why the proxy is information-free at the breakout

`cd3_ratio` distribution over the 1030 breakouts: mean 0.374, 5th percentile **−0.041**,
93.3% > 0, 74.2% > 0.2. A breakout bar by construction *closes beyond the opening-range
edge*, which forces its close into the top (long) or bottom (short) of its own range —
i.e. a positive CLV and therefore a positive directional delta, almost tautologically.
The CLV-delta proxy is **collinear with the breakout condition itself**, so it adds
essentially no independent information. RVOL is independent but simply does not stratify
ORB outcomes — consistent with the prior ORB-quality rejections (B5 prior-day range, B45
OR width, B49 breakout extension): the ORB edge lives in the 4h+ EOD-flatten cohort
(Lesson 88) and resists single-dimension range/volume/momentum filters.

## Decision

**Phase 1 NO-GO → REJECT. No engine variant built.** Variant (a) has no basis (the
proxy doesn't predict outcome and the confirm gate is a near-no-op that removes a better
cohort). Variant (b) is gated off by Phase 1, and is independently contraindicated:
ORB value is concentrated in long EOD-flatten holds (Lesson 88) and early-exit
mechanisms clip the winners that fund the pipeline (B61 confirmed for ORB; B53 for iFVG).

This is the cheap, disciplined outcome the program expects — a clean rejection that costs
no engine code and no test surface. The analysis script is retained for any future
orderflow re-test that arrives with genuinely independent data (tick/L2 delta).
