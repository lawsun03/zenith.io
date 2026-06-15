"""B93: CPI-straddle funded-overlay framing.

Additive overlay: oracle CPI-straddle P&L injected into Phase-B (ORB-reentry
r0.75) equity stream.  Phase-A (iFVG combine, B42 deployed_r1p0) unchanged.

Uses the run_b42_pipeline.py two-phase model:
  sust     = Phase-A combine passes / Phase-B XFA busts
  net/mo   = (xfa_net_per_account - combine_reset_fee) / (cycle_days) * 21

Reports at haircut $200 (canonical) and h0/h400 for sensitivity.
Gap=0 for parity with B42/B57 reference; gap=24 (B86 realistic) also shown.

Usage:
  python scripts/b93_cpi_overlay.py
"""
from __future__ import annotations

import csv
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

import pandas as pd

from app.backtest.funded_sim import (
    daily_pnls_from_equity,
    simulate_combines,
    simulate_xfa_chain,
)

BASELINE        = Decimal("50000")
COMBINE_RESET   = Decimal("150")
DAYS_PER_MONTH  = Decimal("21")
R_FIXED         = Decimal("375")   # 0.75% of $50k = 1R for straddle sizing
YEARS           = ["2021", "2023", "2024", "2025", "2026"]   # 2022 frozen
BARS_1S         = _REPO / "bars/bars_NQ_1s_cpi_windows.csv"

PHASE_A_DIR     = _REPO / "research/equity_b42"
PHASE_A_PREFIX  = "deployed_r1p0"
PHASE_B_DIR     = _REPO / "research/equity_b21"
PHASE_B_PREFIX  = "orb_reentry_r0p75"


# ---------------------------------------------------------------------------
# Equity stitching (mirrors run_b42_pipeline.stitch — offset preserves
# continuity across year boundaries despite each CSV starting at ~$50k).
# ---------------------------------------------------------------------------

def stitch(equity_dir: Path, prefix: str) -> list[tuple[datetime, Decimal]]:
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in YEARS:
        p = equity_dir / f"{prefix}_{year}.csv"
        if not p.exists():
            continue
        seg: list[tuple[datetime, Decimal]] = []
        with p.open(newline="") as f:
            for row in csv.DictReader(f):
                seg.append((datetime.fromisoformat(row["ts"]), Decimal(row["equity"])))
        if not seg:
            continue
        if curve:
            offset = curve[-1][1] - BASELINE
        curve.extend((ts, eq + offset) for ts, eq in seg)
    return curve


# ---------------------------------------------------------------------------
# Oracle extraction
# ---------------------------------------------------------------------------

def _load_oracle_mod():
    spec = importlib.util.spec_from_file_location(
        "news_straddle_cpi_1s", _REPO / "scripts/news_straddle_cpi_1s.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def oracle_per_event(offset_ticks: int = 60, tp_r: float = 3.0) -> list[tuple[date_type, str, float]]:
    """[(event_date, outcome_type, R_value)] for all non-2022 CPI events."""
    mod = _load_oracle_mod()
    df = mod.load_1s(str(BARS_1S))
    ev = pd.read_csv(_REPO / "data/news_events.csv", parse_dates=["ts_utc"])
    ev = ev[
        (ev.event_type == "CPI")
        & (ev.ts_utc.dt.year != 2022)
        & (ev.ts_utc >= df.index[0])
        & (ev.ts_utc <= df.index[-1])
    ]
    results: list[tuple[date_type, str, float]] = []
    for _, e in ev.iterrows():
        r = mod.simulate(df, e.ts_utc, offset_ticks, tp_r)
        results.append((
            e.ts_utc.date(),
            r[0] if r else "nodata",
            float(r[1]) if r else 0.0,
        ))
    return results


# ---------------------------------------------------------------------------
# Phase-B overlay injection
# ---------------------------------------------------------------------------

def inject_straddle(
    daily_b: list[tuple[datetime, Decimal]],
    events: list[tuple[date_type, str, float]],
    r_fixed: Decimal = R_FIXED,
) -> tuple[list[tuple[datetime, Decimal]], int]:
    """
    Add straddle P&L to Phase-B daily series.  Returns (modified, n_standalone).
    n_standalone = CPI dates with fills but no base-engine trade that day.
    """
    straddle: dict[date_type, Decimal] = {
        d: Decimal(str(round(r, 8))) * r_fixed
        for d, outcome, r in events
        if outcome not in ("nofill", "nodata")
    }
    idx: dict[date_type, int] = {ts.date(): i for i, (ts, _) in enumerate(daily_b)}
    result = list(daily_b)
    standalone = 0
    for d, pnl_usd in straddle.items():
        if d in idx:
            ts, base = result[idx[d]]
            result[idx[d]] = (ts, base + pnl_usd)
        else:
            fake_ts = datetime(d.year, d.month, d.day, 14, 0, tzinfo=timezone.utc)
            result.append((fake_ts, pnl_usd))
            standalone += 1
    result.sort(key=lambda x: x[0])
    return result, standalone


# ---------------------------------------------------------------------------
# Pipeline economics
# ---------------------------------------------------------------------------

def phase_stats(daily: list[tuple[datetime, Decimal]], haircut: int = 200, gap: int = 0) -> dict:
    hc = Decimal(str(haircut))
    c = simulate_combines(daily, haircut=hc)
    x = simulate_xfa_chain(daily, haircut=hc, combine_gap_days=gap)
    td = len(daily)
    avg_days_attempt = Decimal(str(td)) / Decimal(str(c["attempts"])) if c["attempts"] else Decimal("0")
    attempts_per_funded = (
        Decimal(str(c["attempts"])) / Decimal(str(c["passes"])) if c["passes"] else Decimal("9999")
    )
    combine_days = attempts_per_funded * avg_days_attempt
    reset_fee = attempts_per_funded * COMBINE_RESET
    avg_funded_days = (
        Decimal(str(td)) / Decimal(str(x["accounts"])) if x["accounts"] else Decimal("0")
    )
    net_per_account = (
        x["net_payouts"] / Decimal(str(x["accounts"])) if x["accounts"] else Decimal("0")
    )
    return {
        "c_passes": c["passes"], "c_busts": c["busts"],
        "c_combine_days": float(combine_days), "c_reset_fee": float(reset_fee),
        "x_accounts": x["accounts"], "x_busts": x["busts"],
        "x_net_payouts": float(x["net_payouts"]),
        "x_net_per_account": float(net_per_account),
        "x_avg_funded_days": float(avg_funded_days),
        "trading_days": td,
    }


def pipeline_economics(a: dict, b: dict) -> dict:
    """Two-phase pipeline economics: Phase-A combine speed × Phase-B XFA earnings."""
    reset_fee   = Decimal(str(a["c_reset_fee"]))
    xfa_net     = Decimal(str(b["x_net_per_account"]))
    net_cycle   = xfa_net - reset_fee
    cycle_days  = Decimal(str(a["c_combine_days"])) + Decimal(str(b["x_avg_funded_days"]))
    net_per_day = net_cycle / cycle_days if cycle_days else Decimal("0")
    net_per_mo  = float(net_per_day * DAYS_PER_MONTH)
    sust = (
        float(Decimal(str(a["c_passes"])) / Decimal(str(b["x_busts"])))
        if b["x_busts"] else float("inf")
    )
    return {"$/mo": net_per_mo, "sust": sust, "cycle_days": float(cycle_days)}


def _line(label: str, eco: dict, a: dict, b: dict) -> str:
    sust = f"{eco['sust']:.2f}x" if eco["sust"] != float("inf") else "inf"
    return (
        f"  {label:22s} ${eco['$/mo']:6.0f}/mo  sust={sust}  "
        f"A-passes={a['c_passes']}  B-busts={b['x_busts']}  "
        f"B-net=${b['x_net_payouts']:.0f}  cycle={eco['cycle_days']:.1f}d"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("B93: CPI-straddle funded-overlay framing")
    print("=" * 56)

    # -- Oracle --
    print("\n[1] 1s oracle (offset=60t)")
    ev3 = oracle_per_event(60, 3.0)
    ev4 = oracle_per_event(60, 4.0)

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
            f" | PF={pf:.2f} | mean={mean_r:.3f}R | {yrs_pos}/{len(yr_map)} yrs+"
        )
        for yr in sorted(yr_map):
            tot = sum(yr_map[yr])
            sign = "+" if tot >= 0 else ""
            print(f"    {yr}: {len(yr_map[yr])} trades  {sign}{tot:.2f}R")

    oracle_summary(ev3, "tp_r=3")
    oracle_summary(ev4, "tp_r=4")

    # -- Load Phase A & B equity --
    print("\n[2] Loading equity curves")
    curve_a = stitch(PHASE_A_DIR, PHASE_A_PREFIX)
    curve_b = stitch(PHASE_B_DIR, PHASE_B_PREFIX)
    print(f"  Phase A: {len(curve_a)} pts  {curve_a[0][0].date()}..{curve_a[-1][0].date()}")
    print(f"  Phase B: {len(curve_b)} pts  {curve_b[0][0].date()}..{curve_b[-1][0].date()}")
    daily_a = daily_pnls_from_equity(curve_a)
    daily_b = daily_pnls_from_equity(curve_b)
    print(f"  Phase A: {len(daily_a)} trading days | Phase B: {len(daily_b)} trading days")

    # -- Build overlay --
    daily_ov3, n_inj3 = inject_straddle(daily_b, ev3)
    daily_ov4, n_inj4 = inject_straddle(daily_b, ev4)
    n_fills3 = sum(1 for _, o, _ in ev3 if o not in ("nofill", "nodata"))
    b_dates   = {ts.date() for ts, _ in daily_b}
    ev3_dates = {d for d, o, _ in ev3 if o not in ("nofill", "nodata")}
    print(f"\n  CPI fills: {n_fills3} | overlap w/ Phase-B trade days: {len(ev3_dates & b_dates)} | standalone injected: {n_inj3}")

    # -- Run two-phase sims --
    print("\n[3] Two-phase pipeline results")
    print("  Ref: B42=$549/mo sust=3.23x  B57=$566/mo sust=3.54x  B90a-switch=$565/mo sust=3.38x")

    for haircut in (200, 0, 400):
        print(f"\n  --- haircut ${haircut} ---")
        for gap in (0, 24):
            a_stats  = phase_stats(daily_a, haircut, gap)
            b_base   = phase_stats(daily_b, haircut, gap)
            b_ov3    = phase_stats(daily_ov3, haircut, gap)
            b_ov4    = phase_stats(daily_ov4, haircut, gap)
            eco_base = pipeline_economics(a_stats, b_base)
            eco_ov3  = pipeline_economics(a_stats, b_ov3)
            eco_ov4  = pipeline_economics(a_stats, b_ov4)
            d3   = eco_ov3["$/mo"] - eco_base["$/mo"]
            d4   = eco_ov4["$/mo"] - eco_base["$/mo"]
            db3  = b_ov3["x_busts"] - b_base["x_busts"]
            print(f"\n  gap={gap}")
            print(_line("BASE (no straddle)", eco_base, a_stats, b_base))
            print(_line("OVERLAY tp_r=3",     eco_ov3,  a_stats, b_ov3)  + f"  d$/mo={d3:+.0f}  dbusts={db3:+d}")
            print(_line("OVERLAY tp_r=4",     eco_ov4,  a_stats, b_ov4)  + f"  d$/mo={d4:+.0f}")

    print("\nDone.")


if __name__ == "__main__":
    main()
