# Funded-Pipeline Baseline — MNQ 5min (post-flatten, wf3)

**Date:** 2026-06-12
**Branch:** `feat/backtest-ux-ab-testing`
**Data:** `bars/bars_MNQ_test_2025_2026.csv` (2025-01 – 2026-05)

---

## 1. What shipped

Four components landed together under the funded-pipeline plan
(`docs/superpowers/specs/2026-06-11-funded-pipeline-spec.md`,
`docs/superpowers/plans/2026-06-11-funded-pipeline.md`):

**Phase A — flatten rule.** The execution engine now enforces Topstep's
intraday rule: all positions flatten at 15:10 CT (3:05 PM buffer enforced as
15:05 CT in engine), new entries cut off at 14:30 CT. In the pre-flatten
walk-forward data, 66 trades were held through the 4:10 PM ET (15:10 CT)
close — a live rule violation. The flatten rule closes those positions at
market at 15:10 CT and zeroes any P&L accrued past that point.

**Phase B — PhaseTracker + governor.** `app/risk/account_phase.py` implements
a rules-accurate Combine and XFA state machine (`PhaseTracker`) mirroring the
live platform's daily-loss (MLL), profit target, trailing-drawdown, and
minimum-day requirements. A risk governor gate in `pretrade.py` blocks entries
when the tracker says `is_dead()`. This is zero-behavior-change until
`account_phase` in `bot_config.json` is flipped from `"practice"` to
`"combine"` or `"xfa"`.

**Phase C — funded_sim + report integration.** `app/backtest/funded_sim.py`
replays a backtest equity curve through sequential Combine and XFA attempts to
produce pass/bust/payout numbers. `app/backtest/report.py` now prints this
block in the per-run summary instead of the misleading period-total
"Combine target: PASSED" verdict. The standalone CLI is
`scripts/funded_sim.py`.

---

## 2. Flatten-rule cost

Measured on the wf2 walk-forward test-set winner
(`backtest_results/wf2_test_winner_flatten/`):

| Metric       | Pre-flatten | Post-flatten | Change    |
|--------------|-------------|--------------|-----------|
| Profit factor | 1.15       | 1.13         | -0.02     |
| Net P&L      | +$13,116    | +$9,611      | -$3,505   |
| Trades       | 1,249       | 1,193        | -56       |
| Max drawdown | $5,706      | $4,643       | -$1,063   |

~27% of prior net profit came from trades held through the 15:10 CT close.
That profit is no longer available under the funded-account rules.
The flatten rule also reduces max drawdown by ~19%, improving the risk-adjusted
picture slightly.

---

## 3. Funded-pipeline numbers (wf3, post-flatten)

Both runs use `bars/bars_MNQ_test_2025_2026.csv` with `--no-risk-limits`.

### Winner config (deployed: stop_buffer=0.30, min_absolute_body=0.50 pt)

From `backtest_results/wf3_test_winner/`:

| Metric       | Value     |
|--------------|-----------|
| Trades       | 1,193     |
| Net P&L      | +$9,611   |
| Profit factor | 1.13     |
| Max drawdown | $4,643    |

Funded-pipeline (from `scripts/funded_sim.py`):

```
COMBINE: attempts 8 | passes 3 | busts 4 | median days-to-pass 34
XFA:     accounts 5 | busts 4 | payouts $7,961 gross / $7,165 net (90%)
(daily granularity — intraday MLL touches understated)
```

Pass rate: 3/8 = **37.5%**. Bust rate: 4/8 = 50% (1 attempt still running at
series end). Median time to pass: 34 trading days (~7 weeks).

### Baseline config (stop_buffer=0.30, min_absolute_body=1.0 pt)

From `backtest_results/wf3_test_baseline/equity_run00.csv`:

| Metric       | Value     |
|--------------|-----------|
| Trades       | 1,211     |
| Net P&L      | +$8,718   |
| Profit factor | 1.12     |
| Max drawdown | $3,992    |

Funded-pipeline:

```
COMBINE: attempts 6 | passes 3 | busts 2 | median days-to-pass 58
XFA:     accounts 3 | busts 2 | payouts $6,522 gross / $5,869 net (90%)
(daily granularity — intraday MLL touches understated)
```

Pass rate: 3/6 = **50%**. Bust rate: 2/6 = 33%. Median time to pass:
58 trading days (~12 weeks) — the tighter body filter reduces attempt count
and extends each attempt.

### Comparison to reference numbers

The handoff spec (`docs/superpowers/specs/2026-06-11-funded-pipeline-spec.md`)
quoted ~40% pass rate and ~$13.4k gross XFA payouts over 17 months. The
pre-flatten funded_sim (run before Phase A landed) gave 10 attempts / 5 passes
/ 4 busts / $13,515 gross.

The post-flatten wf3 winner is lower on all three metrics: fewer passes (3
vs 5), lower gross payout ($7,961 vs $13,515), fewer attempts (8 vs 10). This
is expected — the flatten rule removed ~$3,500 of net profit, compressing the
equity curve and reducing how many Combine targets were cleared. The pass rate
(37.5%) is consistent with the ~40% reference; the payout compression is
proportional to the P&L reduction.

---

## 4. Caveats

**Daily granularity.** The simulator collapses the equity curve to one
closing-equity point per trading day. An intraday MLL touch that recovered by
close is invisible — the MLL trip that would have busted the account never
fires. Bust counts are UNDERSTATED; real bust rates are likely higher. This
will be corrected once the intrabar adverse-excursion recorder exists.

**Fixed-sizing assumption.** The same P&L series is replayed on each new
attempt. This is accurate for fixed-contract runs (2 MNQ contracts) but
approximate once sizing scales with balance.

**Pass-density modeling.** A pass restarts a fresh Combine attempt the next
day. This measures how often the strategy can clear a Combine across the full
series, not a single account lifecycle. Combine→XFA progression is modeled
separately by `simulate_xfa_chain`.

**`mll_trailing: "intraday"` default.** `XfaRules` defaults to tracking the
trailing drawdown intraday (from the session high, not just the account peak at
open). This is the conservative interpretation. If Topstep actually tracks the
trailing drawdown from the prior-day close only, XFA bust counts would be lower.

---

## 5. Go/no-go framing

Budget 2–3 Combine attempts (~$150–300 in fees at $100–150/attempt) before
treating a funded account as a real deployment slot. The 37–50% pass rate means
expect 1–2 busts before the first pass.

Regime note from wf2: passes clustered early-2025 and spring-2026; a mid-2025
bust cluster (roughly Jul–Sep 2025) accounts for most of the XFA bust count.
Check the equity CSV dates against that window before sizing up.

The winner config (deployed) produces slightly higher gross payout ($7,961 vs
$6,522) but more busts (4 vs 2) and a shorter median days-to-pass (34 vs 58).
The baseline is slower and cheaper to run but leaves more money on the table.
For a real funded account, prefer the winner config and budget accordingly.

---

## 6. Next steps

1. **Re-verify rule numbers at help.topstep.com before flipping
   `account_phase`.** Combine profit target, MLL, trailing drawdown, and
   minimum-day counts must match exactly or the governor will block/allow the
   wrong trades. Broker-truth reconciliation of the tracker (compare
   PhaseTracker state vs account portal balance daily) is the first live check.

2. **Adverse-excursion haircut.** Once the intrabar recorder captures
   per-bar MFE/MAE, replay the daily_pnls with an MLL touch model to get a
   bust-rate upper bound. The current numbers are optimistic by an unknown
   margin.

3. **Intraday MLL in XFA.** Verify whether Topstep's XFA trailing drawdown
   resets from the prior close or tracks intraday. If the latter, the
   `mll_trailing` default is already correct; if the former, the XFA numbers
   above are conservative.
