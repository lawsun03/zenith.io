# chop_breakout — REJECTED on the spec's own criteria (2026-06-12)

Built per `CHOP_BREAKOUT_SPEC.md`: compression gate (rolling 20-bar range
< P30 of trailing 100, held 12 bars; vwap_cross alt selectable) → CHOP
with widening bounds → continuation signal when a displacement bar
closes outside the pre-breakout range AND inverts an FVG at the breached
boundary; stop = tighter of FVG far side vs chop midpoint; target =
nearest 15m swing with the 1.5R floor; hard exits (failed-breakout K=6,
VWAP invalidation, optional SMA21 trail) via a new generic strategy-exit
channel in the engine. All six spec tests + ordering regression +
engine-channel test (15 tests). `engine="chop_breakout"`, flat `cb_*`
fields. Fixed defaults, NO sweeps, per spec.

Bug found & fixed during bring-up (zero signals in a year): the
displacement bar widens the chop bounds one bar before its event arrives
(b3-close lag), making `close > chop_high` unsatisfiable. Breakouts are
now judged against the pre-widen bounds snapshot. Regression test pins
the ordering.

## Solo results (frontier sizing 1.25%, full live ruleset)

| period | passes | trades (exits) | run PF | maxDD | long PF | short PF | net |
|---|---|---|---|---|---|---|---|
| test 17mo | 0/17 | 22 | **1.92** | 1090 | 3.31 | 0.21 | +$1,948 |
| 2024 12mo | 0/12 | 13 | **0.09** | 661 | 0.00 | 0.25 | −$2,473 |

## Complementarity matrix (test period, iFVG drought months)

| month | iFVG (control) | chop_breakout |
|---|---|---|
| 2025-03 | 13 tr, +185 | 2 tr, **+1,137** |
| 2025-05 | 5 tr, −668 | 2 tr, **+442** |
| 2025-06 | 3 tr, −1,841 | 2 tr, +27 |
| 2025-10 | 3 tr, −1,519 | 2 tr, +15 |
| 2026-03 | 4 tr, −1,429 | 4 tr, **+939** |
| 2026-04 | 3 tr, −1,710 | 2 tr, **+1,024** |
| 2025-08 | 15 tr, +102 | 4 tr, −908 |
| 2025-01/04/11, 2026-01 | droughts | 0 trades |

Drought-month net on test: **+$2,676** — the complementarity shape the
spec hoped for is genuinely visible on 2025–26.

## Verdict: REJECT (the spec's criteria, applied as written)

- Success required **PF ≥ 1.05 solo** — 2024 is PF 0.09 (every active
  month negative; combined-period PF ≈ 0.89). The spec's regime-honesty
  guardrail (always report 2024) exists precisely for this: a PF that
  flips 1.92 → 0.09 across periods on 13–22-exit samples is regime
  noise, not edge.
- Volume is terminal for the Combine use-case anyway: ~1.3 trades/mo
  (the triple conjunction — 12-bar compression hold + iFVG at the
  boundary + 1.5R floor — rejects nearly everything). Even at test-PF
  it cannot reach +$3k/month.
- Per the spec: "If results disappoint, the answer is reject, not
  tune." No funded_sim combined run (gated on success), no parameter
  rescue.

## MGC addendum (same spec, fixed defaults, run 2026-06-12)

| period | passes | trades (exits) | run PF | net | months with 0 trades |
|---|---|---|---|---|---|
| test 18mo | 0/18 | **5** | 0.34 | −$804 | 13 of 18 |
| 2024 12mo | 0/12 | 13 | 0.20 | −$1,539 | 4 of 12 |

REJECT, trivially — no period shows edge and volume is near-nonexistent.
Gold's 2025–26 trend regime rarely satisfies the 12-bar compression
hold, and the iFVG-at-boundary conjunction almost never follows it. The
MNQ test-period drought sparkle does not appear on MGC at all. (UI:
`chop_mgc_test`, `chop_mgc_2024`.)

## Salvage notes (not actions)

1. The 2025–26 drought-month signal (+$2.7k across 6 winners / 1 loser)
   is the only engine-candidate so far to show positive expectancy
   *specifically* in iFVG droughts other than ORB. If a future engine
   round revisits compression, start from the 2024 failure months, not
   from more filters.
2. The **strategy-exit channel** in the engine (runner.exit_request →
   cancel_all+flatten when in position) is generic, tested, and stays —
   any future engine gets rule-based exits for free.
3. Engine bench after five candidates: iFVG-MNQ + ORB-MNQ remain the
   only two with validated edge; the two-account plan is unchanged.
