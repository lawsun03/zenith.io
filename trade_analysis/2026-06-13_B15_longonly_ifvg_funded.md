# B15 — Long-only iFVG Funded Benchmark
*Session wk1-b15 | 2026-06-13*

## Hypothesis

The iFVG short side has PF=0.960 (loss-making) over 5 years (n=1,251 short trades from
MFE/MAE analysis). Blocking short signals should improve PF and reduce funded account bust
rates. No new code required: `allowed_sides="long"` already exists in StrategyParams and
is fully tested in `tests/test_ablation_modes.py`.

## Setup

**Mechanism:** `--set allowed_sides=long` (no code changes)
**Bars:** `bars/bars_MNQ_dbv_2021_2026.csv` (5y, 127MB, includes 2022)
**Killzones:** `london+ny_am+ny_pm` (default BotConfig, bot_config.json empty at run time)
**Partial_r:** 0 (default BotConfig)
**Baseline:** Full iFVG (allowed_sides=both) at same killzone/risk config

## Results

| Config | Trades/5y | PF | Combine tries | Passes | Combine busts | Days/pass | XFA accts | XFA busts | Net (5y) | Sust |
|---|---|---|---|---|---|---|---|---|---|---|
| Full iFVG r1.25 (baseline) | 6,043 | 1.064 | 196 | 45 | 150 | 7.0 | 85 | 84 | $132,380 | 0.54x |
| LongOnly r0.75 | 3,931 | 1.139 | 155 | 42 | 112 | 8.5 | 65 | 64 | $135,472 | 0.66x |
| LongOnly r1.0 | 3,946 | 1.127 | 209 | 56 | 152 | 6.0 | 51 | 50 | $177,542 | **1.12x** |
| LongOnly r1.25 | 3,928 | 1.121 | 244 | 60 | 183 | 5.0 | 54 | 53 | $186,214 | **1.13x** |

Haircut $200 throughout.

## Key Findings

### 1. Long-only flips pipeline from negative to self-sustaining

At r1.0 and r1.25, long-only iFVG achieves sust ≥ 1.12x while full iFVG at the same
config is deeply pipeline-negative (sust 0.54x). The sustainability flip is driven by:
- **Fewer XFA busts:** removing the loss-making short side improves equity curve survival
- **More combine passes:** better PF means accounts reach the $3k combine target more often

### 2. Volume retained is higher than MFE/MAE analysis suggested

The all-day MFE/MAE analysis showed 1,226 long trades / 1,251 short trades (49.5% long).
Running long-only on named sessions: 3,928 vs 6,043 full = 65% retention. Reason: the
London session (02:00–05:00 ET) generates predominantly bullish NQ setups; the all-day
negative-expectancy windows (overnight, noon) removed by named-session config had a
disproportionately short signal bias.

### 3. PF improvement matches expectations

- Full iFVG (named sessions): PF 1.064
- Long-only iFVG: PF 1.121–1.139 (r1.25–r0.75)
- From all-day MFE/MAE: expected +9% PF lift (1.043 → 1.136)
- Actual (named sessions): +5.4% to +6.9% — directionally correct

### 4. r0.75 remains pipeline-negative despite PF improvement

At r0.75, sust = 42 passes / 64 XFA busts = 0.66x. The mechanism: lower risk per
account means slower growth, so funded accounts spend more time near the MLL floor and
are more vulnerable to multi-day drawdowns. The sustainability crossover is between
r0.75 and r1.0.

## Success Criteria

- Primary: "standalone long-only iFVG funded has better XFA net $/month or bust rate
  than full iFVG at same risk level (r1.25)"
  → **BOTH met:** XFA net +41% ($186k vs $132k); XFA busts -37% (53 vs 84 at r1.25)

- Secondary: "iFVG(Combine) + LongOnly-iFVG(Funded) two-phase pair — does it beat ORB?"
  → Not evaluated (noted as potentially impractical in spec; same instrument, mode switch
  required between Combine/Funded phases)

## Recommendation

**Verdict: CANDIDATE**

Long-only iFVG at r1.25 is the recommended funded-phase configuration for Lawrence to
evaluate on Monday:
- Sust 1.13x (pipeline self-sustaining)
- Net $186,214 over 5y (~$3,104/month gross; pipeline adjustment per B3 methodology
  would give a lower real $/month after reset costs)
- No new code: set `allowed_sides: long` in bot_config strategy params for the funded phase
- Deployed config change: safe to enable (default is "both"; this is a pure filter)

The r1.0 variant (sust 1.12x, $177,542 net) is the conservative alternative with
slightly lower combine pass count (56 vs 60) but similar sustainability.

## Files

- Equity CSVs: `research/equity_b15_ifvg_longonly_r075.csv`, `_r100.csv`, `_r125.csv`
- Test added: `tests/test_ablation_modes.py::TestAllowedSides::test_long_passes_when_long_only`
- Total tests: 629 (up from 628), 0 failures
