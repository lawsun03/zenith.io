# VWAP mean-reversion engine — no edge on MNQ 5min (2026-06-12)

Third candidate engine (ranked #2 after ORB in the second-engine memo).
Build: `app/strategy/vwap.py` — session-anchored VWAP + volume-weighted
σ-bands; closed-bar fade entries (target = VWAP at entry, stop =
stop_sigma·σ beyond); per-side episodic re-arm on VWAP retouch as the
trend-day regime filter. `engine="vwap"` + `vwap_anchor_et` /
`vwap_band_sigma` / `vwap_stop_sigma` on StrategyParams. Tests:
`tests/test_vwap.py` (8). Harness/ruleset identical to the ORB campaign;
risk 1.25%; sweep on 2024 MNQ (train). UI registry `vwap_*`.

## 2024 sweep (12 configs): run PF (passes)

| anchor / stop | band 2.0σ | band 2.5σ | band 3.0σ |
|---|---|---|---|
| 9:30, stop 1.0σ | **1.00** (1) | 0.96 (1) | 0.80 (2) |
| 9:30, stop 1.5σ | 0.83 (0) | 0.72 (1) | 0.75 (1) |
| 18:00, stop 1.0σ | 0.82 (1) | 0.92 (1) | 0.50 (0) |
| 18:00, stop 1.5σ | 0.79 (0) | 0.66 (0) | 0.56 (1) |

Best cell is exactly PF 1.00; eleven of twelve are net-losing. No row,
anchor, band width, or stop width shows structure. The occasional passes
sit on PF<1 (lockout-asymmetry luck, same as MGC ORB). Zero MLL fails
(soft buffer did its job). **Stop rule: no test-period validation.**

## Read

1. **MNQ 5min does not mean-revert off VWAP bands** — it trends through
   them. Wider bands (3.0σ) and wider stops both make it worse, the
   signature of fading a trending instrument: the further price is from
   VWAP, the more likely it keeps going. This is the same regime fact
   from the other side: the two engines that DO work on NQ (iFVG, ORB)
   are both momentum/breakout shaped.
2. The "anticorrelated, feeds on chop" hypothesis from the second-engine
   memo is rejected for this construction. A meaningfully different MR
   design (e.g. multi-day anchors, vol-regime gates, time-of-day
   windows) would be a new research program, not a tweak — and the
   ablation campaign's lesson is that added filters on a PF≤1 base
   rarely rescue it.
3. The two-account plan (iFVG-MNQ + ORB-MNQ) is unchanged. The bench of
   ranked engine candidates is now exhausted: ORB shipped, VWAP-MR
   rejected, master-branch kz_levels remains the only untested candidate
   (cheap funded_sim benchmark, same sweep DNA caveat).

## Caveats

- 2024-only (train); per the stop rule the test period was not spent on
  a no-edge config. 12 cells is a small grid, but the gradient is
  uniform in both swept dimensions.
- VWAP engine code stays in the repo default-off (`engine="vwap"`),
  same status as the ablation mode switches.
