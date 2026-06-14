# B73 — ORB×iFVG directional alignment gate in the Phase A combined engine

**Session:** wk5-b73 (2026-06-14) · **Verdict: REJECTED** (stop rule fired)

## Hypothesis

Phase 1 (Lesson 103, wk2-r5) found that ORB signals preceded by ≥1 same-direction
iFVG that day have PF=1.427 (n=595) vs PF=0.963 (n=435) for those with no same-direction
prior iFVG — ratio 1.48×, consistent 5/5 years. B56 tested this gate as an **ORB-only
Phase B** filter and it failed by volume starvation (Lesson 109: 51/52 accounts bust,
18.7d avg life — the gate cut 42% of an already-sparse ORB stream). B73 re-tests the
**same gate in the Phase A combined engine**, where iFVG fires on all days regardless,
so suppressing ~8–9% of Phase A signals (the conflicted ORB minority) should not starve
the account.

## No code needed

The gate already ships: `StrategyParams.orb_ifvg_alignment_required` (the B56 alignment
gate), wired into `CombinedRunner(alignment_gate=...)` in both `app/backtest/runner.py`
and `app/main.py`, with the suppression logic in `DailySessionContext.gate_b56_orb_suppressed`
(`app/strategy/combined.py`) and the ORB-side guard in `app/strategy/orb.py`. The B73
spec proposed a new `orb_require_ifvg_alignment` param with identical semantics; per
Rule 2/Rule 8 it was reused rather than duplicated. The 5 B73 defining-behaviors are
covered by the existing `tests/test_orb_ifvg_alignment.py` (7 passing).

## Step 1 — Combine harness (61 months, deployed config)

`run_monthly_combine.py --bars bars/bars_MNQ_dbv_2021_2026.csv` with the deployed
config (engine=combined, close, all-day, partial=1.5, risk=1.0%, MNQ body=5.0/stop=3.0/
r=3.5/orb_r=2.5), gate off vs on. Both run this session for self-consistency.

| Config | Passes/61 | Run PF | Long PF | Short PF | Short net |
|--------|-----------|--------|---------|----------|-----------|
| Baseline (no gate) | 10 (16%) | 1.06 | 1.32 | 0.84 | −$20,214 |
| **Gate ON** | **11 (18%)** | **1.12** | 1.30 | **0.97** | **−$3,848** |

The gate **improves** the monthly harness: +1 pass, run PF 1.06→1.12, and short PF
0.84→0.97 — it removes loss-making ORB shorts that fire on days when the day's iFVG
structure points the other way. Step 1's success criterion ("passes improve") is met.

## Step 2 — Two-phase funded pipeline (per-year, 2022 excluded, haircut $200)

Phase A = gate-enabled combined config (`equity_b73/`, generated this session at both
r=3.5 and r=2.5). Phase B = B21 ORB-reentry r0.75 (`equity_b21/`, unchanged: 13/14
busts, $3,131/acct). The gate is orthogonal to the iFVG `r_multiple`, so it is tested
on both the deployed r=3.5 (vs B42) and the B57-candidate r=2.5 (vs B57) to isolate the
gate's effect from the r_multiple choice.

| Config | A passes/att | Reset$/funded | $/mo | Sust |
|--------|--------------|---------------|------|------|
| B42 baseline (r3.5, no gate) | 42/159 | $568 | $549 | 3.23× |
| **B73 gate (r3.5)** | **38/146** | $576 | **$541** | **2.92×** |
| B57 baseline (r2.5, no gate) | 46/167 | $545 | $566 | 3.54× |
| **B73 gate (r2.5)** | **41/149** | $545 | **$559** | **3.15×** |

**Gate effect (isolated, same r):**
- r=3.5: Δ$/mo −$7, Δsust −0.31× → **BOTH WORSE** vs B42
- r=2.5: Δ$/mo −$7, Δsust −0.38× → **BOTH WORSE** vs B57

Success criterion (beat B57 on BOTH $/mo and sust): **not met.**
Stop rule (worse than B42 on BOTH metrics): gate r3.5 is $541 < $549 **and** 2.92× <
3.23× → **stop rule fires.** 2022 holdout not required (result does not beat B57).

## Why the harness and pipeline disagree (Lesson 83, again)

The combine harness has 61 fixed calendar-month slots; removing loss-making conflicted
ORB shorts lets one more month cross the $3k threshold and lifts PF. The funded pipeline
runs **continuous attempts** instead. The gate removes ORB trade volume → less daily
P&L variance → each combine attempt takes longer to resolve → fewer total attempts fit
in 5 years (159→146 at r3.5, 167→149 at r2.5) → **fewer absolute passes** (42→38,
46→41). Per-attempt pass rate is essentially unchanged (26.0% vs 26.4%; 27.5% vs 27.5%),
so the gate adds no per-attempt quality in continuous operation — it only thins the
stream. Since Phase B busts are fixed at 13, sustainability scales directly with Phase A
passes, and fewer passes mechanically lowers sust.

This is the same throughput-vs-rate decoupling documented in Lessons 83 (combined engine),
130 (B71 allowed_sides=long), 105 (Silver Bullet), and 109 (B56 ORB-only). The B73 prior
(~40%) explicitly flagged this risk; it materialized.

## Verdict & recommendation

**REJECTED.** Do not enable `orb_ifvg_alignment_required` on the deployed bot. The gate
is a genuine *monthly* quality improvement (use it if the objective were ever the combine
harness in isolation) but a *pipeline* regression. B57 (remove the MNQ r_multiple override
→ base r=2.5, both-sides, no gate) remains the only clean improvement over B42 and the
standing Monday recommendation. The gate code stays default-off.
