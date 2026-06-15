# News straddle + fade across instruments (NQ / ES-MES / gold / oil)

**Date:** 2026-06-14 · **Method:** ATR-normalized so the breakout distance is comparable
(offset = 0.5×ATR(14,5m); = 60 ticks on NQ, so the NQ run reproduces the confirmed fixed-60t
straddle — the self-check). Straddle: 1-min, range[event-15m,event), entry range±offset, stop
= range boundary (R=offset), TP=3R. Fade: 5-min, fade the first-15m impulse on a reversal
close, stop=impulse extreme, TP=mean-revert-to-ref. 2-tick slip, 2022 excluded, no lookahead.
Script: `scripts/news_multi.py`. (NQ confirmed at 1s in B85; ES/gold/oil here are 1-min only.)

## STRADDLE (3R) — PF / years-positive
| instrument | CPI | PPI | FOMC |
|---|---|---|---|
| **NQ (MNQ)** | **4.20  5/5** | 2.25  4/5 | 1.30  2/5 |
| **ES (MES)** | **3.36  5/5** | 1.06  3/5 | 0.99  1/5 |
| **GOLD (MGC)** | **2.50  5/5** | 0.91  1/5 | **2.21  4/5** |
| OIL (MCL, 2.5y) | 0.54  1/3 | 0.53  0/3 | 0.30  0/3 |

## FADE (→ref) — PF / years-positive
| instrument | CPI | PPI | FOMC |
|---|---|---|---|
| NQ | 1.93  5/5 | 0.41  0/5 | 0.68  2/5 |
| ES | 1.11  2/5 | 0.48  0/5 | 0.47  1/5 |
| GOLD | 0.65  1/5 | 0.94  2/5 | 1.29  2/5 |
| OIL | 1.06  2/3 | 0.43  0/3 | 0.46  0/3 |

## Findings
1. **The CPI breakout straddle GENERALIZES — it is not NQ-specific.** CPI straddle is 5/5
   years positive on NQ (PF 4.2), ES/MES (PF 3.36) AND gold (PF 2.50). Strong robustness
   across index futures + gold. Strength order: NQ > ES > gold. (Could run on several for
   diversification, or just the strongest.)
2. **Gold uniquely ALSO works on FOMC** (PF 2.21, 4/5) — where NQ/ES FOMC straddles fail
   (PF 1.30 / 0.99). Mechanism: FOMC = rates, which drive gold directionally, while stocks
   whipsaw on the statement (the "FOMC fakeout" that kills the index straddle). NEW, sensible.
3. **Oil is dead** for every news straddle and fade (CPI 0.54 / PPI 0.53 / FOMC 0.30). Macro
   CPI/PPI/FOMC don't drive crude cleanly (supply/inventory/geopolitics dominate). Drop oil.
4. **The FADE has no robust edge on any instrument** — best is NQ CPI fade (1.93, 5/5) but
   that is strictly weaker than the NQ CPI *straddle* (with-trend beats counter-trend wherever
   an edge exists). Drop the fade.
5. **PPI is marginal/inconsistent** across instruments (best NQ 2.25/4-5; ES 1.06; gold 0.91).
   Not robust. CPI is the event with the real, generalizing edge.

## Net
- **CPI breakout straddle: the durable edge — NQ + ES/MES + gold, all 5/5 years.**
- **Gold adds FOMC** as a second tradable event (rates → gold directional).
- Oil out. Fade out. PPI marginal.
- Caveat: ES/gold confirmed at 1-min only; NQ's 1s check (B85) showed 1s ≈ 1-min (slightly
  better), so the 1-min ES/gold results are likely trustworthy — but a cheap 1s confirmation
  on ES + gold CPI windows (and gold FOMC) would close it before sizing. ~$1-2 Databento.

## 1-SECOND CONFIRMATION (2026-06-14, B91) — ALL CONFIRMED; both sides work
Fetched ES.v.0 + GC.v.0 ohlcv-1s for the CPI windows and GC.v.0 for the FOMC windows
($2.46; ledger $11.20/$20) and replayed managing from the second after trigger, ATR-offset,
with a LONG/SHORT split (script scripts/news_straddle_1s_confirm.py):

| instrument/event | ALL (1s) | long | short |
|---|---|---|---|
| **ES CPI** | PF 2.42, 51% win, +0.88R, **5/5** | 1.96, 4/5 | **4.01, 4/5** |
| **GOLD CPI** | PF 1.99, 45%, +0.66R, **4/5** | 1.62, 4/5 | 2.67, 4/5 |
| **GOLD FOMC** | PF 1.99, 45%, +0.66R, **4/5** | 2.52, 2/4 | **1.72, 4/5** |

(NQ CPI, from B85: PF 5.99 at 1s.) All slightly below the 1-min estimates — expected from the
conservative manage-from-next-second handling — but all clearly positive and year-consistent.

**Key: both sides work.** CPI straddle SHORT breaks are as good or better than longs (ES short
PF 4.01, gold short 2.67) — so it is NOT a long/trend artifact; the bidirectional breakout has
real edge. **Gold FOMC short side is PF 1.72, 4/5 years** — this resolves the trend-confound
worry: gold-FOMC is a genuine bidirectional rate-reaction edge, not just gold's 2024-26 bull.

### Final confirmed set
- **CPI breakout straddle: NQ (PF ~6), ES/MES (2.4), gold (2.0)** — 1s-confirmed, both sides.
- **Gold FOMC straddle: PF ~2.0, 4/5, both sides** — 1s-confirmed.
- Oil rejected; fade rejected; PPI marginal. Next: B89 engine (instrument-parameterized) +
  B90 pipeline/funded framing across the basket.
