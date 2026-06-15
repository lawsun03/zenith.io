# Handoff: Funded-Pipeline Mode (Combine → Express Funded Account)

Context: the post-fix walk-forward (`wf2_test_*`) is profitable over the full
period (+$9,971 baseline / +$11,678 winner on the equity curve, commissions
included) but does NOT respect Topstep 50K rules. Simulated against actual
rules, the same trade sequence produces ~40% Combine pass rate (4 passes /
4–6 busts across 9–11 sequential attempts) and an XFA lifecycle of 5 accounts
(4 busts) extracting ~$13.4k gross payouts over 17 months. The bot needs an
account-phase-aware risk governor and a rules-accurate simulator so backtests
report pass/bust/payout numbers instead of period-total P&L.

⚠️ Topstep changed rules 8 times between Nov 2025 and Apr 2026. Re-verify every
number below against help.topstep.com before hardcoding. Put all rule numbers
in config, never in logic.

---

## Rule summary (verified 2026-06-11)

### 50K Trading Combine
- Start $50,000. Profit target +$3,000.
- Maximum Loss Limit (MLL): $2,000 trailing. Official help-center examples show
  EOD-balance trailing (ratchets up at end of day, never down). Some 2026
  sources describe intraday trailing. **Implement both as a config switch
  (`mll_trailing: "eod" | "intraday"`) and treat intraday as the conservative
  default for sizing decisions.** MLL touch (realized OR unrealized) = account
  dead. This is the only hard rule.
- Consistency objective: best single day ≤ 50% of total profit at the time the
  target is hit. Violating doesn't fail you — it delays the pass until the
  ratio is satisfied.
- Daily Loss Limit: NOT enforced on TopstepX (removed Aug 2024 for new
  Combines). Keep the bot's own soft-DLL buffer anyway — it's been working.
- Flat by 3:10 PM CT (4:10 PM ET); trading resumes 5:00 PM CT. Trading the
  overnight/London sessions is allowed; HOLDING through 4:10 PM ET is not.
- Fees: ~$49/mo (50K) + $149 activation on pass (standard path).

### Express Funded Account (XFA) — what a pass turns into
- Starts at **$0 balance**. "50K" = buying power, not balance.
- MLL starts at −$2,000, trails EOD, **locks permanently at $0 once balance
  reaches +$2,000**. Balance below MLL = account dead.
- Two payout paths, chosen at activation, cannot switch:
  - **Standard:** payout-eligible after 5 winning days of ≥$150 net each.
  - **Consistency:** eligible after ≥3 winning days with consistency ratio
    (largest winning day ÷ total net profit) ≤ 40%.
- Payout: up to 50% of balance per request, capped (legacy $5,000 Standard /
  $6,000 Consistency; accounts from No-Activation-Fee Combines created after
  2026-04-28 are capped at $2,000/$3,000 — VERIFY which applies). Min $125.
  Winning-day counter resets each payout cycle; the request day doesn't count.
- 90/10 split (trader/Topstep) from dollar one for accounts created after
  2026-01-12.
- Trading day = 5:00 PM CT → 3:10 PM CT next day. Same flatten rule.
- Up to 5 XFAs can run concurrently. Back2Funded allows up to 2 paid
  reactivations if an XFA dies before its first payout.

---

## Data-grounded reality (from wf2_test_winner equity, 2025-01 → 2026-05)

- 373 trading days: mean +$31/day, **median −$1/day**, best +$1,739, worst
  −$1,168. 54% of days are negative. Only **32% of days clear the $150
  winning-day bar** — eligibility accrues in bursts, not steadily.
- Combine sim: 9–11 attempts → 4 passes, 4–6 busts. Passes cluster in
  early-2025 and spring-2026 regimes; mid-period is 4–6 consecutive busts.
- XFA sim (Standard path, payout at 5 win-days & bal ≥ $3k): 5 sequential
  XFAs, 4 busts, $13,417 gross payouts. XFA #3 and #4 bled 155 and 59 days
  with zero payouts — same bad regime as the Combine bust cluster.
- 66 trades held through the 4:10 PM ET flatten window (rule violation today).

Implications: the edge is real but thin and regime-dependent; the pipeline's
job is to harvest the good regimes cheaply and lose slowly in the bad ones.

---

## Build spec

### 1. `account_phase` state machine (new: `app/risk/account_phase.py`)
Phases: `combine` | `xfa` | `live` | `practice` (current behavior = practice).
Config block in `bot_config.json`:

```json
"account_phase": "combine",
"phase_rules": {
  "combine": {
    "starting_balance": 50000, "profit_target": 3000,
    "mll_distance": 2000, "mll_trailing": "intraday",
    "best_day_cap_frac": 0.45,
    "stop_at_target": true
  },
  "xfa": {
    "starting_balance": 0, "mll_distance": 2000, "mll_lock_at": 2000,
    "winning_day_threshold": 150,
    "payout_path": "standard",
    "payout_winning_days": 5,
    "payout_request_floor": 3000, "payout_cap": 5000,
    "payout_fraction": 0.5
  }
}
```

The phase tracker maintains: balance, EOD high-water, current MLL, today's
P&L, winning-day count (current payout cycle), best-day / total-profit ratio.
It must reconcile from broker truth on startup (reuse reconciler patterns) —
never trust local state across restarts.

### 2. Risk governor (extends `app/risk/pretrade.py`)
Hard gates, deterministic, no AI overrides (CLAUDE.md Rule 5):

a) **MLL cushion governor.** cushion = balance − MLL.
   - cushion < $1,000 → halve per-trade risk.
   - cushion < $500 → block new entries (survive to the next EOD ratchet/day).
   - Sizing must guarantee worst-case single-trade loss (stop distance ×
     size + slippage allowance) ≤ 40% of cushion.

b) **Stop-at-target (combine).** Once profit ≥ target AND best-day ratio
   ≤ 50%: block all new entries, flatten, alert. A pass given back is the
   most expensive possible outcome.

c) **Best-day cap (combine).** If today's P&L ≥ best_day_cap_frac × (total
   profit + today's P&L), stop for the day. Prevents one monster day from
   delaying the pass via the consistency objective.

d) **Winning-day lock (xfa).** If today's P&L ≥ $150 and < $150 + one
   avg-loss, the next loser turns a winning day into a nothing day. Policy:
   once today ≥ $300 (2× threshold), tighten: only A-grade setups for the
   rest of the day. Do NOT stop at exactly $150 — median day is −$1, so
   upside days must be allowed to run; just don't let a +$160 day decay
   to +$140.

e) **Post-payout cushion (xfa).** After a payout, remaining balance IS the
   entire cushion (MLL locked at $0). Resume at half risk until balance
   rebuilds to payout_request_floor.

f) **Flatten rule (all phases).** Hard-flatten all positions by 4:05 PM ET
   (buffer before 4:10); block entries after ~3:30 PM ET whose expected hold
   would span the close. DST-correct: use exchange timezone
   (America/Chicago, 3:10 PM CT), not fixed UTC offsets.

### 3. Rules-accurate simulator (new: `scripts/funded_sim.py`)
Input: any equity CSV (per-fill) or trades CSV from the backtest harness.
Simulates sequential Combines and the XFA lifecycle with the same
`phase_rules` config the live governor uses (one source of truth — import the
same module, don't duplicate the rule math).

Output per run:
```
COMBINE: attempts N | passes P | busts B | median days-to-pass | bust streaks
XFA:     accounts N | busts B | payouts $X gross / $Y net (90%) | days to first payout
PIPELINE: expected $ per 12 months net of fees, per regime segment
```
Wire into `app/backtest/report.py` so every backtest summary prints these
INSTEAD of the current period-total "Combine target: PASSED" line (that line
is misleading — remove it).

Granularity caveat to document in the script: per-fill equity misses
open-trade unrealized excursion, so MLL touches are UNDERSTATED. If the
intrabar recorder (2026-05-26 plan) captures MFE/MAE per trade, use it to
bound the error; otherwise add a configurable adverse-excursion haircut.

### 4. Tests (Rule 9 — encode intent)
- Governor blocks entry when worst-case loss > 40% of cushion; allows at 39%.
- Stop-at-target: profit crosses target with ratio ok → entries blocked,
  flatten called. Ratio violated → entries still allowed (pass delayed, not
  failed).
- XFA winning-day accounting: +$149 day ≠ winning day; +$150 = winning day;
  payout resets the counter; request-day excluded.
- MLL lock: balance touches +$2,000 intraday but closes below — verify
  whether lock is EOD or intraday per current docs, then test that exact
  semantic.
- Flatten: position open at 4:04 PM ET on a DST boundary day → flattened.
- Sim ↔ governor parity: feeding the sim's bust sequence through the governor
  prevents the bust (cushion gates fire first).

### 5. Sequencing
1. Flatten rule (it's a live rule violation today — ship first).
2. Phase tracker + governor, behind `account_phase: "practice"` default
   (zero behavior change until opted in).
3. funded_sim.py + report integration; re-run wf2 configs and record the
   pass/bust/payout table in `trade_analysis/`.
4. Only then: decide whether to start a paid Combine, using the sim's
   per-regime numbers, not period totals.

---

## Goals, stated plainly (what "success" means per phase)

**Combine phase goal:** reach +$3,000 with best day ≤ 50%, never touching
MLL, in as few trading days as possible — then STOP. Not "maximize P&L."
Expected per the data: ~40% of attempts pass, median ~50–60 days; budget
~2–3 attempts (~$150–300 in fees) per funded account.

**XFA phase goal:** (1) survive from $0 to +$2,000 to lock the MLL at break-
even — this is the single most valuable milestone in the pipeline, it makes
the account unkillable below zero; (2) accumulate 5×$150 winning days;
(3) extract 50%-of-balance payouts whenever balance ≥ $3k, leaving the other
half as cushion; (4) in bad regimes, the goal degrades to "lose slowly" —
half-size, A-grades only, keep the account alive for the next good regime.
Expected per the data: ~$12k net to trader per 17 months at current sizing,
across ~5 account lifecycles — improvable primarily by the cushion governor
(turning 155-day zero-payout bleeds into cheap hibernation).
