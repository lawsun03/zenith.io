# sweep+BOS and order-block variants — both rejected (2026-06-12)

Lawrence's request: test (1) Revelio's simple chain verbatim — sweep +
break of structure, NO displacement/FVG leg (`engine="sweep_bos"`,
`app/strategy/sweep_bos.py`: sweep arms; a close beyond the nearest
opposing confirmed swing within 10 bars fires the reversal; entry = BOS
close, stop past sweep extreme, instrument r_multiple) and (2) an
order-block fallback zone (`confirmation="ob_fallback"`: iFVG signals
bit-identical when an inversion exists — regression-tested — plus new
signals where displacement fires without one, the last opposite candle
supplying the zone). Frontier sizing, both periods. UI: `sbos_*`, `obfb_*`.

## Results vs control

| variant | test 17mo | 2024 | PF (t/24) | trades/mo (t) |
|---|---|---|---|---|
| iFVG control | **6 (35%)** | **4 (33%)** | 1.31 / 1.37 | 25 |
| sweep_bos (no FVG) | 2 (12%) | 2 (17%) | 1.23 / **0.92** | ~30 |
| iFVG + ob_fallback | 1 (6%) | 1 (8%) | 1.17 / **0.98** | ~12* |

\* exits collapse because added losers freeze months early. Zero MLL
fails everywhere.

## Reads

1. **sweep_bos: Revelio's "simpler won" does not transfer** — same
   verdict as T5 from the opposite direction. Healthy volume, weak
   edge: PF 0.92 on the regime-honesty year. Familiar shape: a test
   sparkle (its 2 passes are iFVG NON-pass months, including +$22.4k
   in 2025-08 on 119 trades) killed by 2024. Third candidate (after
   ORB, chop) whose occasional wins land in iFVG droughts — the
   complementarity pattern is real and recurring, but no candidate has
   carried it across regimes except ORB.
2. **ob_fallback: the iFVG inversion is the quality filter, not a zone
   convenience.** Diluting it with OB zones (displacement-without-
   inversion fires often) added PF≈1 trades that ate the DPL/soft-buffer
   budget and crowded out five of iFVG's six test passes. 2024 shorts
   PF 0.60. This is T5's lesson confirmed from the additive direction:
   loosening the confirmation adds bad trades, not good ones.
3. Standing answer to "does iFVG use BOS/OB?": no, and after today,
   deliberately not — BOS-instead-of-iFVG fails on regime honesty;
   OB-alongside-iFVG actively harms.

Per the stop rule: both rejected, no tuning. The engine bench remains
iFVG-MNQ + ORB-MNQ on two accounts.
