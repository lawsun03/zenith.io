# Gates

Ordered cheapest-first. Each gate **rejects**; nothing promotes a candidate except clearing all
of them. Every gate writes its measured value, the threshold applied, and pass/fail into the
ledger's `gate_results` — including gates that were never reached.

A candidate that fails gate N is not re-run against later gates. `first_failed_gate` is the
single most useful analytics column in the ledger; keep it accurate.

| # | Gate | Rejects when | Typical cost |
| --- | --- | --- | --- |
| 0 | IR validity | Schema invalid, or `ir_hash` already present in the ledger | µs |
| 1 | Frequency floor | Fewer than 3 trades (NQ+ES+GC combined) in more than 20% of weeks, or any gap > 10 calendar days | ms |
| 2 | Cost survival | Edge per trade < 2× modelled round-turn cost | ms |
| 3 | Parameter plateau | Best parameters sit on a spike rather than a plateau in the sweep surface | s |
| 4 | Walk-forward | Any fold's OOS return negative, or Sharpe decay worse than 60% | s |
| 5 | Sharpe floor | Observed OOS Sharpe below the cutoff for the current trial count (table below) | s |
| 6 | Deflated Sharpe / PBO | Fails significance given trial count; or loop-level PBO > 0.5, which blocks **all** promotion | min |
| 7 | Sharpe ceiling | Observed Sharpe > 1.5 — flag for investigation, do not auto-promote | s |
| 8 | Combine simulator | Any DLL breach, trailing MLL breach, or consistency failure across Monte Carlo trade-order resamples | min |
| 9 | Teachability | Entry depends on any predicate marked `recognizable: false` | s |
| 10 | Human review | Manual. The only point the holdout may be opened. | — |

## Gate 1 — frequency floor

Measured on the **distribution**, not the mean. A strategy averaging 3/week by clustering 20
trades in volatile stretches and nothing for a month fails, correctly.

```
weeks_meeting_floor = (# weeks with >= 3 trades) / (# weeks in sample)
PASS if weeks_meeting_floor >= 0.80 AND max_gap_days <= 10
```

Record the weekly histogram in the candidate summary, not just the scalar.

This gate does a lot of work: it eliminates rare-event strategies automatically, which resolves
macro-release contamination without a special-case filter (8 CPI prints a year cannot produce
3 trades a week).

**Do not relax this gate when candidates get scarce.** A frequency floor is pressure toward
overtrading; gate 2 is what stops that becoming a fee-generation scheme.

## Gate 2 — cost survival

Round-turn cost per instrument is commission + exchange/clearing fees + 1 tick of slippage on
entry + 1 tick on exit + a human-latency penalty (see `cost-model.md`). Require **2×** headroom,
not 1×, because the slippage model is optimistic.

```
PASS if mean_edge_per_trade >= 2.0 * modelled_round_turn_cost
```

Express cost in Sharpe-ratio units as well as currency, so it is comparable across instruments
of different volatility.

## Gate 5 — Sharpe floor, by trial count

The cutoff needed so that there is only a ~5% chance of accepting a rule that is truly
unprofitable. Rows are the number of rules tested; columns are years of data. Read the 16-year
column by interpolating between 10 and 30.

| Rules tested | 1 yr | 5 yr | 10 yr | 30 yr |
| --- | --- | --- | --- | --- |
| 1 | 1.5 | 0.7 | 0.5 | 0.4 |
| 5 | 2.3 | 1.1 | 0.8 | 0.5 |
| 10 | 2.8 | 1.2 | 0.8 | 0.6 |
| 50 | 3.4 | 1.5 | 1.0 | 0.6 |
| 100 | 3.4 | 1.5 | 1.1 | 0.7 |

Source: Carver, *Systematic Trading*, table 4.

The trial count comes from the ledger at the moment of testing, and is written to
`sr_cutoff_applied` so the decision is reproducible later.

**This table is the reason for the 30–50/year cap.** At 100 trials on 16 years the cutoff is
around 1.0, while a genuinely good rule has true Sharpe near 0.3 — you would reject every real
rule and accept only flukes.

## Gate 6 — deflated Sharpe

`research.stats.deflated_sharpe.deflated_sharpe_ratio` (Bailey & López de Prado, 2014). It
reproduces the paper's worked example, DSR = 0.9004, in `tests/test_statistics.py`. Pass
threshold: 0.95.

The expected-maximum benchmark SR0 needs **V[{SR_n}], the variance of the per-trade Sharpe
ratios across the trials searched**. It is a property of the whole search, like loop-level PBO,
so the caller computes it once per loop state and passes `sr_variance_across_trials`:

- `empirical_sr_variance(trial_sharpes)`: the paper's value. Prefer it once enough trials
  exist to measure the spread.
- `null_sr_variance(n_obs)` = 1/(n_obs − 1): the spread under the zero-skill null. It's a
  principled stand-in while the ledger is too thin, but it's usually more lenient than the
  empirical value.

It is required whenever n_trials > 1. There is deliberately no fallback. Using the SR estimate's
own sampling variance instead reports 0.98 where the paper's answer is 0.90.

## Gate 7 — Sharpe ceiling

Not a rejection, a **hold**. Observed Sharpe above 1.5 on this kind of system is more often a
defect than an edge. Check, in order:

1. Negative skew — frequent small gains, rare large losses. Compute skew of returns; flag if < −0.5.
2. Lookahead — is any predicate reading a bar it could not have seen? Any regime label leaking in?
3. Cost model — is the fill assumption too kind, especially around the open and macro releases?
4. Sample — does the result rest on a handful of sessions? Check the top-5 trade contribution.

Realistic reference points: a systematic rule on a single instrument is worth ~0.40 Sharpe;
across instruments ~0.80; a well-diversified professional futures system backtests near 1.0.
No systematic hedge fund in Carver's sample sustained above 1.0 for more than a few years.

## Gate 8 — combine simulator

Take the candidate's OOS trade sequence, resample its order several thousand times, and run each
path through the account rules in `accounts/topstep-50k.json`. Report the fraction of paths that
reach the profit target without breaching the daily loss limit or the trailing threshold.

```
combine_payout_prob = (# paths reaching target without breach) / (# paths)
PASS if combine_payout_prob >= 0.60
```

Rank by this number, not by Sharpe. A strategy that pays out in 70% of orderings beats a
higher-Sharpe strategy that pays out in 40%.

## Gate 9 — teachability

Every predicate in the IR carries `recognizable: true|false`. Entry conditions may compose only
from recognizable predicates. Exits and stops may use non-recognizable ones (the framework
handles those for the trader).

## After the gates: blend, do not rank

Survivors are added to an equal-weighted ensemble within their strategy family. The ensemble is
what the combine simulator scores and what the trainer teaches.

**Any code that sorts survivors by performance and promotes the top N is a bug.** Selection
scored 0.07 Sharpe against 0.33 for blending in Carver's gold experiment; picking at random beat
picking the best. The gates exist to remove garbage, not to rank.

## Macro releases

Every candidate is backtested twice — release sessions included and excluded. Both runs must
clear every gate, and the candidate's recorded score is the **worse** of the two. Recorded this
way it is one trial. If you were to report whichever flatters, it would be two, and the trial
count would silently understate the real search.
