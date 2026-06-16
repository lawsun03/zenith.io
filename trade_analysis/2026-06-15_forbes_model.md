# Forbes Model — backtest & verdict (2026-06-15)

**Verdict: REJECT.** The ICT "Forbes Model" (trade the NY open toward session liquidity via
opening-range FVGs) measures a **16% win rate** vs the author's claimed **75–80%**, negative
expectancy in every config and 4 of 5 years, and it **never passes a combine** ($0 funded
payout). The published 75–80% is post-hoc selection bias on annotated winners, exactly as
hypothesized. Spec: `docs/superpowers/specs/2026-06-15-forbes-model-design.md`. Harness:
`scripts/run_forbes_backtest.py`. Engine: `engine="forbes"` (`app/strategy/forbes.py`),
backtest-only, default-off — ships unwired.

## Headline (default: KZ 09:30–10:30 ET, OR 09:30+15m, liquidity target, min_rr 1.4)
5y MNQ 1-min `bars_MNQ_dbv_2021_2026.csv`, 2022 holdout excluded. PaperBroker intrabar fill,
stop-first on same-bar (conservative).

| period | n | win% | exp (R) | net | maxDD |
|---|---|---|---|---|---|
| **overall** | 524 | **16.0%** | **−0.453** | **−$1,002** | $1,495 |
| 2021 | 68 | 13.2% | −0.607 | −$267 | $501 |
| 2023 | 130 | 14.6% | −0.293 | −$555 | $564 |
| 2024 | 136 | 19.1% | −0.327 | +$297 | $186 |
| 2025-26 | 190 | 15.8% | −0.597 | −$477 | $736 |

Win rate **16.0%** is nowhere near the 75–80% claim → claim **not supported empirically**.

## Ablations
- **(a) Killzone:** ON 16.0% (−$1,002) vs OFF 15.8% (−$903) — the time filter barely matters; both lose.
- **(d) min_rr sweep:** 1.0 → 16.9% / −$1,679 ; 1.4 → 16.0% / −$1,002 ; 2.0 → 14.0% / **−$218** (least-bad but still negative).
- **(e) stop_mode:** beyond_wick −$1,002 vs beyond_or −$1,460 (wick better).
- **(c) target_mode:** only `liquidity` measured; `or_top`/`midway_poi` are unimplemented (0 trades) — the "safe target" variant is untested.

## Funded pipeline (haircut 0/200/400)
combine **0/1–0/2 pass** (never passes the +$3k objective), XFA **net payout $0**. Non-viable as a funded strategy.

## Honesty caveats (do not change the verdict)
1. **0% stand-aside.** The OR-must-contain-FVG "choppy-day filter" never fires (1245/1247 days lock an OR,
   1244 are eligible) — the engine takes a trade **every** eligible day (524 trades / 524 days). So this
   implementation **over-trades** vs a faithful Forbes. But min_rr=2.0 (far fewer, more selective trades)
   is *still* negative (−$218, 14% win), so more selectivity does not flip it.
2. **Safe-target untested.** `or_top`/`midway_poi` return None → skip; only the aggressive liquidity target
   was measured. The 75–80% gap is far too large for a safer target to close.

## Why it loses (mechanism)
Median trade is ≈ −1.1R; winners (16%) are too rare to pay for the 84% that stop near −1R. Targeting the
"nearest unswept opposing session liquidity" is reached only on the occasional trend day; most NY-open
attempts get stopped at the swept wick / OR boundary first. This is the standard fate of post-hoc-annotated
ICT setups under objective, look-ahead-free backtesting (cf. the rejection graveyard in research/LESSONS.md).

## Disposition
Engine kept default-off / backtest-only (no live wiring). No further build justified. If ever revisited,
the two caveats above (real FVG-presence filter, safe-target modes) are the only open variables — but the
16% win rate makes a positive flip implausible. Discretionary assumptions used are documented in the spec.
