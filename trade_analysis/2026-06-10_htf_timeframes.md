# Higher-Timeframe Test — 5min / 15min, 2.5 years (2026-06-10)

Same live config, same data (Jan 2024 → May/Jun 2026), bars resampled by
`load_bars_csv`. Clean sim (fill fix + cancel_all fix). `--no-risk-limits`.
Artifacts: `backtests/{mgc,mnq,mes}_{5min,15min}_v3.json`.

## Results (vs 1min baseline)

| run | entries | win% | PF | equity end ($50k start) | maxDD |
|---|---|---|---|---|---|
| MGC 1min | 7,981 | 36.5 | 0.84 | −$5.0k | $56.7k |
| MNQ 1min | 10,929 | 35.7 | 0.86 | −$13.6k | $64.3k |
| MES 1min | 7,133 | 33.0 | 0.71 | −$12.8k | $63.5k |
| MGC 5min | 1,795 | 40.3 | 0.78 | $19.8k | $32.0k |
| **MNQ 5min** | **1,952** | **42.8** | **1.07** | **$53.7k (+$3.7k)** | **$8.8k** |
| MES 5min | 1,718 | 40.4 | 0.81 | $26.9k | $23.3k |
| **MGC 15min** | **618** | **37.4** | **1.03** | **$50.6k (+$0.6k)** | **$5.8k** |
| MNQ 15min | 715 | 35.6 | 0.96 | $46.3k | $10.1k |
| MES 15min | 620 | 37.9 | 0.81 | $40.7k | $9.4k |

Yearly equity deltas (from balance curve — per-trade rows double-count partials):

- **MNQ 5min: 2024 −$4.9k, 2025 +$5.3k, 2026 +$3.2k** — positive the last 1.5 years
- MGC 15min: −$1.3k, +$1.3k, +$0.6k — marginal, improving
- MNQ 15min: −$1.5k, −$5.3k, +$3.1k — inconsistent

## Read

**Timeframe is the highest-leverage dimension found so far.** The 1min strategy
bleeds everywhere (PF 0.71–0.86); on 5/15min the same rules range from mildly
negative to mildly positive, with max drawdown an order of magnitude smaller.
The reversal logic appears to capture something real on bigger bars that 1min
noise destroys.

**Skepticism owed:** 6 configurations tested → one thin winner (PF 1.07) can be
selection luck. The MNQ 5min edge is +$1.9/trade after modeled costs (1-tick
slippage + $0.74/side) — real-world slippage variance could erase it. 2024 was
negative even for the best run.

**Untuned-parameter note:** absolute-unit params don't rescale (`min_absolute_body`
1.0pt is weak on 15min bars; `stop_buffer` 0.30 relatively tight;
`sweep_window_bars` 10 = 50/150 min). These results are "same rules, bigger bars"
— a proper HTF param pass might improve them (or reveal the edge was the loose gate).

## Next steps

1. Param re-tune for 5min (stop buffer, min body, sweep window) — small sweep
2. Walk-forward MNQ 5min (train 2024, validate 2025–26) before believing PF 1.07
3. If it survives: live config change is `timeframes: ["5min"]` — bot restart needed
