# B97 — Full-strategy re-run on MGC + MES (instrument-transfer confirmation)

**Session:** wk7-b97 · 2026-06-15 · model:opus
**Verdict: CONFIRMATORY REJECT.** No MGC or MES configuration produces materially
positive net payouts at a sustainable bust rate. The iFVG / ORB session-structure
edge does **not** transfer to micro gold or micro S&P 500. This confirms the
documented prior (combined iFVG+ORB on MES/MGC = REJECTED; all instruments lose).

---

## Method

- **Data (on disk, no Databento fetch):** MGC = `bars_MGC_GCv_2021_2026.csv` (5y,
  1-min, v-rolled); MES = `bars_MES_ESv_2024_2026.csv` (2.5y, 1-min, v-rolled).
  2022 holdout **excluded** from the funded runs (MES has no 2022 anyway).
- **Variants (fixed configs, NO sweeps):** (a) deployed `engine=combined`
  (`ifvg_entry_mode=close`); (b) `engine=orb` `orb_r_multiple=2.5`; (c) `engine=ifvg`
  (`ifvg_entry_mode=close`). All on 5min, deployed-style: `partial_profit_r=1.5`,
  `killzones=all`, `swing_stop_lookback=30`.
- **Override rescaling (the spec's key requirement — do NOT copy MNQ point values).**
  The two MNQ overrides are noise/structure thresholds; rescaled by **%-of-price**
  (house norm, Lesson 3) off median close:
  - MNQ median 16504 → `stop_buffer` 3.0pt = 0.0182%, `min_absolute_body` 5.0pt = 0.0303%.
  - **MGC** (median 1991): stop_buffer **0.40**, min_absolute_body **0.60** (rounded to 0.10 tick).
  - **MES** (median 5986): stop_buffer **1.00**, min_absolute_body **1.75** (rounded to 0.25 tick).
- **Scoring:** funded = `equity_export --risk-pct 0.75 --partial-r 1.5` → `funded_sim`
  (haircut 0/200/400); combine = `run_monthly_combine --risk-pct 1.25` on the
  holdout-clean 2024-2026 files (the combine harness has no exclude-years flag).

## Funded objective (risk 0.75, full available period ex-2022)

| Instr | Engine | trades | net $ | PF | XFA net h200 | XFA busts / accts h200 |
|---|---|---:|---:|---:|---:|---:|
| MGC | combined | 4428 | −54,934 | **0.81** | 2,922 | 50 / 51 |
| MGC | orb | 1352 | −12,016 | **0.91** | 8,883 | 27 / 28 |
| MGC | ifvg | 3341 | −51,115 | **0.79** | 4,482 | 44 / 45 |
| MES | combined | 2513 | −29,029 | **0.85** | 6,581 | 31 / 32 |
| MES | orb | 814 | +2,810 | **1.03** | 14,417 | 16 / 17 |
| MES | ifvg | 1349 | −77,738 | **0.46** | 0 | 27 / 28 |

Every cell busts essentially **all** XFA accounts (1–2 survivors of 17–51). The
"net payouts" are the small gross banked before each account dies — not a
sustainable pipeline.

## Combine objective (risk 1.25, 2024-2026, 29 months — ORB only)

ORB is the structurally strongest engine (Lesson 17), so the campaign-standard
combine harness was run on the two ORB cells; combined/iFVG (PF 0.46–0.85) are
moot.

| Instr | passes | rate | run PF | MLL fails | long PF / short PF |
|---|---|---|---|---|---|
| MES orb | 3 / 29 | 10% | 1.08 | 0 | 1.07 / 1.08 |
| MGC orb | 4 / 29 | 14% | 0.90 | 0 | **0.72** / 1.14 |

MNQ control baseline ≈ 6/17 test (~35%). Both micros fall far short. MGC ORB's
4 "passes" are luck-concentrated single months sitting on a **net-losing** equity
curve (run PF 0.90); gold ORB **longs are loss-making** (PF 0.72), the mirror of
NQ's long-biased structure.

## What we learned

1. **The session-structure edge is NQ-specific, not portable.** iFVG and ORB key
   off the equity-index RTH open/sweep microstructure; gold (macro/USD-driven) and
   S&P (correlated-but-different vol regime) do not reproduce it. MES ORB is the
   only PF>1 cell and it is breakeven (1.03) with a catastrophic pipeline.
2. **Directional bias flips by instrument.** NQ's edge is long-biased ("tops stall,
   bottoms sweep", Lesson 8). On **gold ORB the long side is the loser** (PF 0.72,
   shorts 1.14). A side filter tuned on NQ would be backwards on gold — another
   reason the transfer fails structurally, not by tuning.
3. **Confirms the prior cleanly.** No new engine, gate, or config is justified.
   MGC/MES remain CONFIRMED only on the **event-driven CPI/FOMC straddle** (separate
   mechanism), never on the session engines.

## Guardrails honored

Analysis-only. No code changed; `bot_config.json` / `.env` untouched; nothing
enabled live. No Databento spend (on-disk data). 2022 excluded from funded runs;
combine used holdout-clean 2024-26 files.
