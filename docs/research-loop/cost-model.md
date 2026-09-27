# Cost model

Gate 2 requires **2× headroom** over modelled round-turn cost, not 1×, because every slippage
assumption below is optimistic and you know it.

## Components

Round-turn cost per contract:

```
cost = commission + exchange_fees + clearing_fees
     + (slippage_ticks_entry + slippage_ticks_exit) * tick_value
     + human_latency_penalty
```

## Baseline slippage

| Condition | Slippage assumption |
| --- | --- |
| Limit order, normal session | 0 ticks (but assume partial-fill risk) |
| Market order, normal session | 1 tick each side |
| Stop order | 1.5 ticks each side |
| First minute of RTH | 2 ticks each side |
| Within 2 minutes of a macro release | 3+ ticks each side, or exclude |

## Human latency penalty

**This project has no bot.** A backtest fills at the price the engine says; you will not. Between
recognising a setup, deciding, and clicking, you lose time and price that no standard backtest
models.

Start at **1 additional tick per side** beyond the automated slippage assumption. Tighten it
later using your own drill divergence data from `drill_sessions` — once you have measured how
long you actually take to act, replace the constant with your measured distribution.

This penalty applies to entry only when the entry is discretionary-triggered. Stops and targets
resting on the book do not pay it.

## Express cost in Sharpe units

Currency cost is not comparable across instruments of different volatility. Convert:

```
cost_SR = annual_cost_currency / (account_equity * annual_vol_target)
```

A cost level of 0.113 SR against a 20% volatility target is an annual performance drag of
20% × 0.113 ≈ 2.3%. Report both the currency figure and the SR figure per candidate.

## Instrument reference

Verify against your broker's current schedule before trusting a gate-2 result.

| | Tick size | Tick value | Notes |
| --- | --- | --- | --- |
| MNQ | 0.25 | $0.50 | Primary execution instrument |
| MES | 0.25 | $1.25 | |
| MGC | 0.10 | $1.00 | |
| SIL | 0.005 | $5.00 | Micro silver (1,000 oz) — not `MSI` |
| NQ | 0.25 | $5.00 | Research series only |
| ES | 0.25 | $12.50 | Research series only |
| GC | 0.10 | $10.00 | Research series only |
| SI | 0.005 | $25.00 | Research series only (5,000 oz) |

## Why this gate matters more than the others

Carver's cost table, for a system whose pre-cost Sharpe is 0.40 at one-month holding:

| Holding period | Theoretical SR | After cost (futures) |
| --- | --- | --- |
| 1 month | 0.40 | 0.37 |
| 1 week | 0.83 | 0.71 |
| 1 day | 1.8 | 1.2 |
| Half a day | 2.6 | 1.4 |
| One hour | 5.2 | **0.28** |

The theoretical Sharpe keeps climbing with trade frequency — Sharpe scales with the square root
of independent bets per year. Costs climb faster. At one-hour holding, a theoretical 5.2 collapses
to 0.28.

Your 3-trades-per-week floor sits near the peak of that curve, which is the good news. The danger
is that a frequency *floor* creates pressure to trade more, and this gate is the only thing
standing between that pressure and a fee-generation scheme. Do not relax it when candidates get
scarce.
