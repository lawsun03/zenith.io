# B96 — 200-day SMA trend regime + RSI-pullback as FEATURES (Phase-1 falsification)

**Session:** wk7-b96 · 2026-06-15 · CHEAP Phase-1 only, no engine built.
(Reclaimed orphan: a prior session crashed on the weekly limit after writing
Lessons 164/165 + the analysis script but before recording/committing.)

**Source:** Lawrence sent a batch of MA/EMA/RSI/Bollinger ideas. Most map to
mechanism classes already rejected (daily-context regime: B5/B35/B65, Lesson 120;
mean-reversion: VWAP-MR / supply-demand, external claims 7-for-7). The two worth a
cheap feature-falsification were the SLOWER-timescale 200-day SMA regime and
RSI-pullback-in-uptrend.

## Method

No engine. Tagged the deployed iFVG+ORB combined per-trade dataset
(`research/mfe_mae_deployed_b88.csv`, MNQ, partials) with a daily-timescale regime
derived from `bars/bars_MNQ_dbv_2021_2026.csv`:

- Daily close → rolling 200-day SMA, Wilder RSI(2) and RSI(14).
- `regime = bull` if daily close > 200-SMA, else `bear`.
- 2022 frozen holdout / SMA warmup excluded → usable span **2023–2026** (4 yrs,
  2026 partial), n=2813 trades.
- Value column = `pnl_usd` (dollar PF), run through
  `scripts/edge_diagnostics.localize` with the **aggregate PF (1.079) as
  control_pf** (a bucket must beat the do-nothing baseline, not just clear 1.20).

Script: `scripts/analyze_b96_sma_rsi.py`.

## H1 — 200-SMA regime × side

Aggregate: n=2813, win 37%, PF 1.079 (control, not robust).

| bucket      |   n  | win | PF   | yrs+ |
|-------------|-----:|----:|------|------|
| bear        | 323  | 37% | 1.19 | 3/4  |
| bull        | 2490 | 37% | 1.06 | 3/4  |
| bear_long   | 174  | 40% | 1.30 | 3/4  ← flagged |
| bull_long   | 1238 | 39% | 1.15 | 4/4  |
| bear_short  | 149  | 34% | 1.08 | 2/4  |
| bull_short  | 1252 | 34% | 0.98 | 1/4  |

`localize` flags `bear_long` (PF 1.30) as a robust sub-edge. **It is not a usable
gate:**

1. **Confounded with edges already exploited.** ORB carries it: of the 174
   bear-long trades, the ORB component (`orb_bear`) is PF 2.18 (n=82); iFVG is
   regime-neutral and slightly negative (PF 0.97 in both regimes). The 200-SMA
   regime does not separate iFVG quality — it surfaces the ORB edge we already
   have.
2. **Volume-fatal.** The `bear` regime is 323/2813 = **11.5%** of trades. Gating
   to it sacrifices ~88% of volume — fatal for the combine objective (Lesson 2:
   volume is the binding constraint).
3. **Backward direction.** The edge concentrates in *downtrend longs*
   (`bear_long` 1.30 > `bull_long` 1.15), the opposite of a trend-follow premise —
   matching **B65**'s finding that NQ regime direction is backward, a hallmark of a
   regime *confound* rather than a tradeable signal.

Regime alone: bear 1.19 / bull 1.06 — neither clears the 1.20 control threshold.

## H2 — RSI-pullback-in-bull-regime (long trades)

| test / bucket          |  n   | PF   | yrs+ |
|------------------------|-----:|------|------|
| RSI(2) v_oversold <10  | 119  | 1.27 | 1/4  | ← Connors pullback; fails year-consistency |
| RSI(2) oversold 10-30  | 218  | 1.18 | 3/4  |
| RSI(14) overbought >70 | 169  | 1.22 | 3/4  | ← momentum, OPPOSITE of hypothesis |
| RSI(14) < 30           | 2    | 0.01 | 0/1  | ← never fires on daily NQ uptrend |

The Connors RSI(2)-oversold pullback (the actual hypothesis) reaches PF 1.27 but is
positive in only **1/4 years** — not robust. The only marginally-robust RSI bucket
is RSI(14) *overbought* (momentum continuation, PF 1.22, 3/4) — the **opposite** of
a mean-reversion pullback, and barely above the 1.20 line, unconfirmed OOS.
Mean-reversion off RSI extremes does not improve intraday quality ("tops stall,
bottoms sweep", Lesson 8, extends to daily RSI).

## Verdict: REJECTED (Phase-1 NO-GO). No engine, no gate built.

The 200-day SMA regime joins the failed daily-context class (B5/B35/B65,
Lesson 120; Lessons 164/165). The slower timescale (200d vs B65's 20d, B35's 1d)
did not rescue the class. RSI-pullback rejected. The Lawrence MA/EMA/Bollinger
*system* ideas remain correctly un-queued (overnight-hold / mean-reversion
classes).
