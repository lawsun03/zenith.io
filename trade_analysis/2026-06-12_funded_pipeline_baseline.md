# Funded-Pipeline Baseline — MNQ 5min (post-flatten, wf3)

**Date:** 2026-06-12
**Branch:** `feat/backtest-ux-ab-testing`
**Data:** `bars/bars_MNQ_test_2025_2026.csv` (2025-01 – 2026-05)

---

## 1. What shipped

Four components landed together under the funded-pipeline plan
(`docs/superpowers/specs/2026-06-11-funded-pipeline-spec.md`,
`docs/superpowers/plans/2026-06-11-funded-pipeline.md`):

**Phase A — flatten rule.** The engine enforces Topstep's close-of-day rule:
all positions hard-flatten at 15:05 CT (a 5-minute buffer before the 15:10 CT
/ 4:10 PM ET deadline), new entries are cut off at 14:30 CT. In the
pre-flatten walk-forward data, 66 trades were held through the deadline — a
live rule violation.

**Phase B — PhaseTracker + governor.** `app/risk/account_phase.py` implements
the Combine/XFA rule state machine (trailing MLL, consistency rule, winning
days, payouts); `pretrade.py` gained five deterministic governor gates
(cushion sizing, stop-at-target, best-day cap, winning-day lock, post-payout
half-risk). Zero behavior change until `account_phase` in `bot_config.json`
is flipped from `"practice"`.

**Phase C — funded_sim + report integration.** `app/backtest/funded_sim.py`
replays a backtest equity curve through sequential Combine attempts and the
XFA lifecycle using the SAME PhaseTracker the live governor uses.
`app/backtest/report.py` prints the pass/bust/payout block in every per-run
summary instead of the misleading period-total "Combine target: PASSED"
verdict. CLI: `scripts/funded_sim.py <equity.csv> [--haircut N]`.

---

## 2. Flatten-rule cost

Measured on the wf2 walk-forward test-set winner
(`backtest_results/wf2_test_winner_flatten/`):

| Metric        | Pre-flatten | Post-flatten | Change  |
|---------------|-------------|--------------|---------|
| Profit factor | 1.15        | 1.13         | −0.02   |
| Net P&L       | +$13,116    | +$9,611      | −$3,505 |
| Trades        | 1,249       | 1,193        | −56     |
| Max drawdown  | $5,706      | $4,643       | −$1,063 |

~27% of prior net profit came from trades held through the close — profit
that is not available under funded-account rules. Max drawdown improves ~19%.

---

## 3. Funded-pipeline numbers (wf3, post-flatten)

Both runs use `bars/bars_MNQ_test_2025_2026.csv` with `--no-risk-limits`.

### Winner config (deployed: MNQ overrides stop_buffer=3.0, min_absolute_body=5.0)

From `backtest_results/wf3_test_winner/`: 1,193 trades, +$9,611, PF 1.13,
maxDD $4,643.

```
COMBINE: attempts 8 | passes 3 | busts 4 | median days-to-pass 34
XFA:     accounts 5 | busts 4 | payouts $7,961 gross / $7,165 net (90%)
```

Pass rate 3/8 = **37.5%** (one attempt still running at series end). Median
time to pass: 34 trading days (~7 weeks).

Haircut sensitivity (assumed $300 intraday adverse excursion below each
day's close): 9 attempts / 3 passes / 5 busts; XFA 7 accounts / 6 busts but
net payouts hold at ~$7.2k — the payout stream is robust to the daily-
granularity optimism.

### Baseline config (base strategy: stop_buffer=0.30, min_absolute_body=1.0)

From `backtest_results/wf3_test_baseline/`: 1,211 trades, +$8,718, PF 1.12,
maxDD $3,992.

```
COMBINE: attempts 6 | passes 3 | busts 2 | median days-to-pass 58
XFA:     accounts 3 | busts 2 | payouts $6,522 gross / $5,869 net (90%)
```

Pass rate 3/6 = **50%**, but median 58 days to pass (~12 weeks) — slower,
cheaper, less productive.

### Comparison to reference numbers

The handoff spec quoted ~40% pass rate and ~$13.4k gross XFA payouts over 17
months; the pre-flatten funded_sim reproduced that (10 attempts / 5 passes /
4 busts / $13,515 gross). The post-flatten winner is lower across the board
(3 passes, $7,961 gross) — expected, since the flatten rule removed ~$3.5k
of (illegal) profit. The pass rate itself (37.5%) remains consistent with
the ~40% reference.

---

## 4. Rule verification vs help.topstep.com (2026-06-11)

Verified against the live help-center articles (Trading Combine Parameters,
Maximum Loss Limit, Consistency, XFA Parameters, Payout Policy):

| Rule | Help center | Our default | Status |
|---|---|---|---|
| Combine: start / target / MLL | $50k / +$3k / $2k trailing, locks at start | same | ✅ |
| MLL monitoring | real-time incl. unrealized; trail examples are EOD | `mll_trailing` switch, intraday default | ✅ conservative |
| Consistency | best day ÷ total profit ≤ 50%; violation RAISES target (best÷0.5), never fails | `target_reached` = profit ≥ target AND best day < profit/2 | ✅ equivalent |
| DLL | optional/self-set (not enforced) | bot's own soft buffer kept | ✅ |
| Flatten | 3:10 PM CT; day = 5 PM CT → 3:10 PM CT | 15:05 CT with buffer | ✅ |
| XFA: start / MLL / lock | $0 / −$2k EOD-trailing / locks $0 at +$2k; reset to $0 after payout | same | ✅ |
| Winning day | net ≥ $150; request day excluded from next cycle | same | ✅ |
| Profit split | 90/10 from dollar one (accounts after 2026-01-12) | 0.90 | ✅ |
| **Payout cap (50K Standard)** | **$2,000** (cut from $5,000 on 2026-04-28) | **corrected 5000 → 2000** | ⚠️ fixed |
| Payout floor | none stated (min payout $125) | $3k floor is the BOT'S own rule (post-payout cushion guard) | ✅ documented |
| Max contracts (50K) | 5 standard / 50 micros | risk config caps at 30 micros | ✅ tighter |

The cap correction does not change the wf3 numbers above — individual
payouts at this sizing run $1.5–2k, under both caps. It binds only if XFA
balances grow past ~$4k before a request.

---

## 5. Caveats

**Daily granularity.** The simulator sees one closing-equity point per
trading day; an intraday MLL touch that recovered by close is invisible.
Bust counts are UNDERSTATED. The `--haircut` flag bounds this (see winner
sensitivity above) until the intrabar recorder measures real excursions.

**Fixed-sizing assumption.** The same P&L series replays on each attempt —
accurate for fixed-contract runs, approximate once sizing scales with balance.

**Pass-density modeling.** A pass restarts a fresh Combine attempt the next
day; this measures how often the strategy clears a Combine across the
series, not one account's lifecycle (XFA progression is the separate chain).

---

## 6. Go/no-go framing

Budget 2–3 Combine attempts (~$150–300 in fees) before treating a funded
account as a real deployment slot; at 37.5% expect 1–2 busts before the
first pass. Passes cluster in the early-2025 and spring-2026 regimes; the
mid-2025 cluster (≈Jul–Sep) produced most busts — losing months bleed
slowly (governor caps them) rather than blowing up. Prefer the winner config
(faster median pass, higher payout stream) and accept its higher bust count.

---

## 7. Remaining work before flipping `account_phase`

1. ~~Re-verify rule numbers~~ — done 2026-06-11 (section 4); payout cap fixed.
2. ~~Broker-truth reconciliation~~ — `reconcile_with_broker` now runs at
   startup for non-practice phases (adopts broker balance, ratchets
   high-water anchors); high-water/best-day/winning-day HISTORY still needs
   `phase_rules["state"]` seeding after a mid-account restart (validated,
   fail-loud on typos).
3. ~~Adverse-excursion haircut~~ — `--haircut` shipped; replace with real
   intrabar MFE/MAE once the recorder exists.
4. On first non-practice start: confirm the logged balance/MLL/cushion match
   the TopstepX dashboard.
