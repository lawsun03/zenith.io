"""B94: Event-calendar router -- stack base + CPI straddle + gold-FOMC straddle.

Day-type routing in the funded-pipeline model: the base engine runs every day;
the CPI straddle (NQ, B85/B93 oracle, fixed 60t offset) is injected ADDITIVELY
on CPI dates; the gold-FOMC straddle (MGC, B91 oracle, ATR offset, tp_r=3) is
injected ADDITIVELY on FOMC dates.  CPI and FOMC dates are disjoint, so the
"router toggled by day type" is exactly: add each straddle's P&L only on its own
event dates.

Multi-instrument equity combination (the spec's hard part) is handled in R-space:
every straddle outcome is normalised to an R-multiple, then sized to the SAME
fixed-fractional dollar risk R_FIXED ($375 = 0.75% of $50k).  So a gold (MGC)
straddle and an NQ straddle both risk $375/event -- the combined funded account's
dollar equity is the base Phase-B curve plus each event-day's R*R_FIXED, regardless
of which instrument produced the R.  This is the correct way to merge MNQ + MGC
P&L into one account stream.

Variants compared (all against B42 base-only):
  1. BASE                 base-only Phase B (ORB-reentry r0.75)         [ref $549/mo 3.23x]
  2. +CPI (additive)      base + CPI straddle on CPI days              [= B93 OV-3R]
  3. +FOMC (additive)     base + gold-FOMC straddle on FOMC days       [NEW]
  4. FULL STACK           base + CPI + gold-FOMC, day-gated            [NEW -- the point]
  5. CPI mode-switch      base OFF on CPI days, straddle only          [= B90a]

Stop rule: a piece that doesn't improve the stack vs base-only on EITHER objective
($/mo or sust) is dropped from the router.  Per-piece attribution reported either way.

Reuses b93_cpi_overlay (stitch / CPI oracle / inject_straddle / pipeline economics).

Usage:  python scripts/run_b94_pipeline.py
"""
from __future__ import annotations

import importlib.util
import sys
from collections import defaultdict
from datetime import date as date_type
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import pandas as pd  # noqa: E402

from scripts.b93_cpi_overlay import (  # noqa: E402
    R_FIXED,
    inject_straddle,
    oracle_per_event,        # CPI oracle (fixed 60t, NQ)
    phase_stats,
    pipeline_economics,
    stitch,
    PHASE_A_DIR, PHASE_A_PREFIX, PHASE_B_DIR, PHASE_B_PREFIX,
)
from app.backtest.funded_sim import daily_pnls_from_equity  # noqa: E402

GOLD_COARSE = _REPO / "bars/bars_MGC_GCv_2021_2026.csv"
GOLD_FOMC_1S = _REPO / "bars/bars_GC_1s_fomc_windows.csv"
GOLD_TICK = 0.1


# ---------------------------------------------------------------------------
# Gold-FOMC oracle (B91): ATR offset, tp_r=3, managed second-by-second.
# ---------------------------------------------------------------------------

def _load_confirm_mod():
    spec = importlib.util.spec_from_file_location(
        "news_straddle_1s_confirm", _REPO / "scripts/news_straddle_1s_confirm.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def gold_fomc_per_event() -> list[tuple[date_type, str, float]]:
    """[(event_date, outcome_type, R_value)] for all non-2022 FOMC events on gold."""
    mod = _load_confirm_mod()
    b5 = mod.to5(mod.load(str(GOLD_COARSE)))
    s1 = mod.load(str(GOLD_FOMC_1S))
    ev = pd.read_csv(_REPO / "data/news_events.csv", parse_dates=["ts_utc"])
    ev = ev[
        (ev.event_type == "FOMC")
        & (ev.ts_utc.dt.year != 2022)
        & (ev.ts_utc >= s1.index[0])
        & (ev.ts_utc <= s1.index[-1])
    ]
    results: list[tuple[date_type, str, float]] = []
    for _, e in ev.iterrows():
        r = mod.simulate(s1, b5, e.ts_utc, GOLD_TICK)
        results.append((
            e.ts_utc.date(),
            r[0] if r else "nodata",
            float(r[1]) if r else 0.0,
        ))
    return results


# ---------------------------------------------------------------------------
# Router primitives (pure -- defining-behavior tested in tests/test_b94_router.py)
# ---------------------------------------------------------------------------

def stack_overlays(
    daily_b: list[tuple[datetime, Decimal]],
    *overlays: list[tuple[date_type, str, float]],
    r_fixed: Decimal = R_FIXED,
) -> tuple[list[tuple[datetime, Decimal]], int]:
    """Additively inject one or more disjoint-date straddle overlays.

    Each overlay is a [(date, outcome, R)] list.  Returns (daily, n_standalone).
    Reuses inject_straddle so a single overlay reproduces B93 exactly.
    """
    out = list(daily_b)
    standalone = 0
    for ev in overlays:
        out, n = inject_straddle(out, ev, r_fixed)
        standalone += n
    return out, standalone


def mode_switch_cpi(
    daily_b: list[tuple[datetime, Decimal]],
    cpi_events: list[tuple[date_type, str, float]],
    r_fixed: Decimal = R_FIXED,
) -> list[tuple[datetime, Decimal]]:
    """B90a mode-switch: on CPI days REPLACE base P&L with straddle-only P&L."""
    straddle: dict[date_type, Decimal] = {
        d: Decimal(str(round(r, 8))) * r_fixed
        for d, outcome, r in cpi_events
        if outcome not in ("nofill", "nodata")
    }
    idx = {ts.date(): i for i, (ts, _) in enumerate(daily_b)}
    result = list(daily_b)
    for d, pnl in straddle.items():
        if d in idx:
            ts, _ = result[idx[d]]
            result[idx[d]] = (ts, pnl)          # replace, not add
        else:
            result.append((datetime(d.year, d.month, d.day, 14, 0, tzinfo=timezone.utc), pnl))
    result.sort(key=lambda x: x[0])
    return result


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

def oracle_summary(events: list, label: str) -> None:
    traded = [(d, o, r) for d, o, r in events if o not in ("nofill", "nodata")]
    wins = [r for _, _, r in traded if r > 0]
    losses = [-r for _, _, r in traded if r < 0]
    pf = sum(wins) / sum(losses) if losses else 99.9
    mean_r = sum(r for _, _, r in traded) / len(traded) if traded else 0.0
    yr_map: dict[int, list] = defaultdict(list)
    for d, _, r in traded:
        yr_map[d.year].append(r)
    yrs_pos = sum(1 for rs in yr_map.values() if sum(rs) > 0)
    print(
        f"  {label}: {len(traded)}/{len(events)} filled "
        f"| {len(wins)}/{len(traded)} wins ({len(wins)/len(traded)*100:.0f}%)"
        f" | PF={pf:.2f} | mean={mean_r:+.3f}R | {yrs_pos}/{len(yr_map)} yrs+"
    )
    for yr in sorted(yr_map):
        tot = sum(yr_map[yr])
        print(f"    {yr}: {len(yr_map[yr])} trades  {tot:+.2f}R")


def _row(label, eco, a, b, base_eco=None, base_b=None) -> str:
    sust = f"{eco['sust']:.2f}x" if eco["sust"] != float("inf") else "inf"
    s = (
        f"  {label:20s} ${eco['$/mo']:6.0f}/mo  sust={sust:>7s}  "
        f"B-busts={b['x_busts']:2d}  B-net=${b['x_net_payouts']:>8.0f}  cyc={eco['cycle_days']:.0f}d"
    )
    if base_eco is not None:
        d = eco["$/mo"] - base_eco["$/mo"]
        db = b["x_busts"] - base_b["x_busts"]
        s += f"  | d$/mo={d:+5.0f}  dbusts={db:+d}"
    return s


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("B94: Event-calendar router -- base + CPI straddle + gold-FOMC straddle")
    print("=" * 72)

    # -- Oracles --
    print("\n[1] Straddle oracles (per-event R, 2022 excluded)")
    cpi_ev = oracle_per_event(60, 3.0)         # NQ CPI, fixed 60t, tp_r=3
    fomc_ev = gold_fomc_per_event()            # gold FOMC, ATR offset, tp_r=3
    oracle_summary(cpi_ev, "CPI (NQ, 60t, 3R) ")
    oracle_summary(fomc_ev, "FOMC (gold, ATR, 3R)")

    # -- Equity curves --
    print("\n[2] Loading Phase-A / Phase-B equity")
    curve_a = stitch(PHASE_A_DIR, PHASE_A_PREFIX)
    curve_b = stitch(PHASE_B_DIR, PHASE_B_PREFIX)
    daily_a = daily_pnls_from_equity(curve_a)
    daily_b = daily_pnls_from_equity(curve_b)
    print(f"  Phase A: {len(daily_a)} trading days | Phase B: {len(daily_b)} trading days")

    b_dates = {ts.date() for ts, _ in daily_b}
    cpi_traded = {d for d, o, _ in cpi_ev if o not in ("nofill", "nodata")}
    fomc_traded = {d for d, o, _ in fomc_ev if o not in ("nofill", "nodata")}
    print(f"  CPI fills={len(cpi_traded)} (overlap base days={len(cpi_traded & b_dates)})  "
          f"FOMC fills={len(fomc_traded)} (overlap={len(fomc_traded & b_dates)})  "
          f"CPI&FOMC same day={len(cpi_traded & fomc_traded)}")

    # -- Phase-B additive variants (Phase A fixed at base; B93 convention; the
    #    per-piece attribution table) --
    daily_b_cpi, _ = stack_overlays(daily_b, cpi_ev)
    daily_b_fomc, _ = stack_overlays(daily_b, fomc_ev)
    daily_b_stack, _ = stack_overlays(daily_b, cpi_ev, fomc_ev)

    # -- Alternative framings --
    #  (i)  CPI switch on Phase A (combine), cf. B90a -- base OFF on CPI combine
    #       days, straddle only.  (B90a used $500/R; B94 uses $375/R, so this is
    #       the within-B94 analog, not a numeric match to B90a's $565/mo.)
    daily_a_cpiswitch = mode_switch_cpi(daily_a, cpi_ev)
    #  (ii) FULL STACK injected into BOTH phases -- the spec's "one account equity
    #       stream": the funded account trades base + both straddles in whichever
    #       phase it is currently in.
    daily_a_stack, _ = stack_overlays(daily_a, cpi_ev, fomc_ev)

    # -- Pipeline --
    print("\n[3] Two-phase pipeline")
    print("    refs: B42 BASE $549/mo 3.23x | B93 +CPI $853/mo 2.80x | B90a switch $565/mo 3.38x ($500/R)")
    for haircut in (200, 0, 400):
        print(f"\n  ===== haircut ${haircut} =====")
        for gap in (0, 24):
            a_base = phase_stats(daily_a, haircut, gap)
            print(f"\n  --- gap={gap} ---")
            # Phase-B additive attribution (Phase A = base)
            base_b = phase_stats(daily_b, haircut, gap)
            base_eco = pipeline_economics(a_base, base_b)
            print(_row("BASE", base_eco, a_base, base_b))
            for label, daily in [("+CPI (additive)", daily_b_cpi),
                                 ("+FOMC (additive)", daily_b_fomc),
                                 ("FULL STACK (PhB)", daily_b_stack)]:
                b = phase_stats(daily, haircut, gap)
                eco = pipeline_economics(a_base, b)
                print(_row(label, eco, a_base, b, base_eco, base_b))
            # Alternative framings
            a_sw = phase_stats(daily_a_cpiswitch, haircut, gap)
            eco_sw = pipeline_economics(a_sw, base_b)
            print(_row("CPI switch (PhA)", eco_sw, a_sw, base_b, base_eco, base_b))
            a_st = phase_stats(daily_a_stack, haircut, gap)
            b_st = phase_stats(daily_b_stack, haircut, gap)
            eco_st = pipeline_economics(a_st, b_st)
            print(_row("STACK (both phases)", eco_st, a_st, b_st, base_eco, base_b))

    print("\nDone.")


if __name__ == "__main__":
    main()
