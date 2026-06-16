"""wk9-holdout-cpi: 2022 confirmatory holdout for B93/B94 CPI straddle overlay.

Protocol step: A finished candidate gets exactly ONE confirmatory 2022 run.
The B93/B94 CPI straddle overlay was shipped live on 2026-06-15; this run
validates the 2022 holdout year using existing 1-min bars and the 2022
equity CSVs from B42 (equity_b42/deployed_r1p0_2022.csv +
equity_b21/orb_reentry_r0p75_2022.csv).

Oracle: 1-min bars (bars_MNQ_dbv_2021_2026.csv), offset=60t, range stop, tp_r=3.
Funded overlay: injects 2022 straddle P&L into Phase-B equity stream.
Mirrors b93_cpi_overlay.py approach exactly; uses same phase_stats /
pipeline_economics logic.
"""
from __future__ import annotations

import csv
import sys
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
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

TICK = 0.25
RANGE_MIN = 15
ENTRY_WINDOW_MIN = 30
MAX_HOLD_MIN = 180
SPREAD_TICKS = 1.0
SLIP_TICKS = 2.0
OFFSET_TICKS = 60
TP_R = 3.0

BASELINE = Decimal("50000")
COMBINE_RESET = Decimal("150")
DAYS_PER_MONTH = Decimal("21")
R_FIXED = Decimal("375")   # 0.75% of $50k = 1R for ORB-reentry sizing

YEARS_5Y = ["2021", "2023", "2024", "2025", "2026"]   # original (excl 2022)
YEARS_6Y = ["2021", "2022", "2023", "2024", "2025", "2026"]  # holdout incl 2022

PHASE_A_DIR = _REPO / "research/equity_b42"
PHASE_A_PREFIX = "deployed_r1p0"
PHASE_B_DIR = _REPO / "research/equity_b21"
PHASE_B_PREFIX = "orb_reentry_r0p75"


# ---------------------------------------------------------------------------
# Equity stitching
# ---------------------------------------------------------------------------

def stitch(equity_dir: Path, prefix: str, years: list[str]) -> list[tuple[datetime, Decimal]]:
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in years:
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
# Oracle simulation (1-min bars, mirrors news_straddle_cpi_sweep.simulate)
# ---------------------------------------------------------------------------

def simulate_1min(df: pd.DataFrame, ts: datetime, offset_ticks: int, tp_r: float):
    offset = offset_ticks * TICK
    slip = SLIP_TICKS * TICK
    hs = (SPREAD_TICKS / 2.0) * TICK
    rng = df.loc[ts - timedelta(minutes=RANGE_MIN): ts - timedelta(minutes=1)]
    if len(rng) < RANGE_MIN - 2:
        return None
    rhigh, rlow = float(rng["high"].max()), float(rng["low"].min())
    buy_stop, sell_stop = rhigh + offset, rlow - offset
    win = df.loc[ts: ts + timedelta(minutes=ENTRY_WINDOW_MIN)]
    if win.empty:
        return None

    side = ""; entry = stop_level = tp_level = None; fill_ts = None
    for tstamp, bar in win.iterrows():
        up, dn = bar["high"] >= buy_stop, bar["low"] <= sell_stop
        if up and dn:
            return ("whipsaw", -1.0)
        if up:
            side = "long"
            entry = max(buy_stop, bar["open"]) + slip + hs
            stop_level = rhigh
            tp_level = entry + tp_r * (entry - stop_level)
            fill_ts = tstamp
            break
        if dn:
            side = "short"
            entry = min(sell_stop, bar["open"]) - slip - hs
            stop_level = rlow
            tp_level = entry - tp_r * (stop_level - entry)
            fill_ts = tstamp
            break
    if not side:
        return None

    r_unit = abs(entry - stop_level)
    hold = df.loc[fill_ts: fill_ts + timedelta(minutes=MAX_HOLD_MIN)]
    for k, (_, bar) in enumerate(hold.iterrows()):
        eb = (k == 0)
        if side == "long":
            hit_stop = (bar["close"] <= stop_level) if eb else (bar["low"] <= stop_level)
            hit_tp = (bar["close"] >= tp_level) if eb else (bar["high"] >= tp_level)
            sx, tx = stop_level - slip - hs, tp_level - hs
        else:
            hit_stop = (bar["close"] >= stop_level) if eb else (bar["high"] >= stop_level)
            hit_tp = (bar["close"] <= tp_level) if eb else (bar["low"] <= tp_level)
            sx, tx = stop_level + slip + hs, tp_level + hs
        if hit_stop:
            return ("stop", ((sx - entry) if side == "long" else (entry - sx)) / r_unit)
        if hit_tp:
            return ("tp", ((tx - entry) if side == "long" else (entry - tx)) / r_unit)

    last = float(hold["close"].iloc[-1]) if len(hold) else entry
    pnl = (last - hs - entry) if side == "long" else (entry - last - hs)
    return ("timeout", pnl / r_unit)


def oracle_for_year(df: pd.DataFrame, year: int) -> list[tuple[date_type, str, float]]:
    ev = pd.read_csv(_REPO / "data/news_events.csv", parse_dates=["ts_utc"])
    ev_year = ev[
        (ev.event_type == "CPI") &
        (ev.ts_utc.dt.year == year) &
        (ev.ts_utc >= df.index[0] + timedelta(hours=2)) &
        (ev.ts_utc <= df.index[-1] - timedelta(hours=2))
    ]
    results = []
    for _, e in ev_year.iterrows():
        r = simulate_1min(df, e.ts_utc, OFFSET_TICKS, TP_R)
        if r is not None:
            results.append((e.ts_utc.date(), r[0], float(r[1])))
        else:
            results.append((e.ts_utc.date(), "nofill", 0.0))
    return results


def oracle_for_years(df: pd.DataFrame, years: list[str]) -> list[tuple[date_type, str, float]]:
    results = []
    for y in years:
        results.extend(oracle_for_year(df, int(y)))
    return results


# ---------------------------------------------------------------------------
# Straddle injection (mirrors b93_cpi_overlay.inject_straddle)
# ---------------------------------------------------------------------------

def inject_straddle(
    daily_b: list[tuple[datetime, Decimal]],
    events: list[tuple[date_type, str, float]],
    r_fixed: Decimal = R_FIXED,
) -> tuple[list[tuple[datetime, Decimal]], int]:
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
# Phase stats and pipeline economics (mirrors b93_cpi_overlay)
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
    reset_fee = Decimal(str(a["c_reset_fee"]))
    xfa_net = Decimal(str(b["x_net_per_account"]))
    net_cycle = xfa_net - reset_fee
    cycle_days = Decimal(str(a["c_combine_days"])) + Decimal(str(b["x_avg_funded_days"]))
    net_per_day = net_cycle / cycle_days if cycle_days else Decimal("0")
    net_per_mo = float(net_per_day * DAYS_PER_MONTH)
    sust = (
        float(Decimal(str(a["c_passes"])) / Decimal(str(b["x_busts"])))
        if b["x_busts"] else float("inf")
    )
    return {"$/mo": net_per_mo, "sust": sust, "cycle_days": float(cycle_days)}


def line(label: str, eco: dict, a: dict, b: dict) -> str:
    sust = f"{eco['sust']:.2f}x" if eco["sust"] != float("inf") else "inf"
    return (
        f"  {label:30s} ${eco['$/mo']:6.0f}/mo  sust={sust}  "
        f"A-pass={a['c_passes']}  B-bust={b['x_busts']}"
    )


def main():
    bars_path = _REPO / "bars/bars_MNQ_dbv_2021_2026.csv"
    df = pd.read_csv(bars_path, parse_dates=["ts"]).set_index("ts").sort_index()

    print("=" * 60)
    print("wk9-holdout-cpi: 2022 CPI straddle holdout validation")
    print("=" * 60)

    # --- 2022 oracle ---
    print("\n[1] 1-min oracle on 2022 CPI events (offset=60t, range-stop, tp_r=3):")
    rows_2022 = oracle_for_year(df, 2022)
    traded = [(ts, o, r) for ts, o, r in rows_2022 if o != "nofill"]
    wins = [r for _, _, r in traded if r > 0]
    losses = [r for _, _, r in traded if r < 0]
    gw = sum(wins); gl = -sum(losses)
    pf_2022 = gw / gl if gl > 0 else 99.9
    wr_2022 = len(wins) / len(traded) * 100 if traded else 0.0

    print(f"  Events: {len(rows_2022)}  filled: {len(traded)}  nofill: {len(rows_2022)-len(traded)}")
    if traded:
        print(f"  WR: {wr_2022:.1f}%  PF: {pf_2022:.2f}  mean R: {sum(r for _,_,r in traded)/len(traded):.3f}")
    print(f"\n  Per-event:")
    for ts, outcome, r in rows_2022:
        mark = "+" if r > 0 else ("-" if r < 0 else "=")
        print(f"    {ts}  {outcome:9s}  {r:+.3f}R  {mark}")

    print(f"\n  Gate (PF>=3.0): {'PASS' if pf_2022 >= 3.0 else 'WEAK (PF<3.0)'}")

    # Also run 5y oracle for cross-check
    print("\n[2] 5y oracle cross-check (excl 2022, same 1-min bars):")
    rows_5y = oracle_for_years(df, YEARS_5Y)
    t5 = [(ts, o, r) for ts, o, r in rows_5y if o != "nofill"]
    w5 = [r for _, _, r in t5 if r > 0]; l5 = [r for _, _, r in t5 if r < 0]
    pf5 = sum(w5) / -sum(l5) if l5 else 99.9
    print(f"  Events: {len(rows_5y)}  filled: {len(t5)}  WR: {len(w5)/len(t5)*100:.1f}%  PF: {pf5:.2f}")
    print(f"  (B93 reference: 1s oracle PF=5.99, 67% WR -- 1-min expected slightly lower)")

    # --- Load equity curves ---
    print("\n[3] Loading equity curves...")
    curve_a5 = stitch(PHASE_A_DIR, PHASE_A_PREFIX, YEARS_5Y)
    curve_b5 = stitch(PHASE_B_DIR, PHASE_B_PREFIX, YEARS_5Y)
    curve_a6 = stitch(PHASE_A_DIR, PHASE_A_PREFIX, YEARS_6Y)
    curve_b6 = stitch(PHASE_B_DIR, PHASE_B_PREFIX, YEARS_6Y)
    print(f"  Phase A 5y: {len(curve_a5)} pts | Phase B 5y: {len(curve_b5)} pts")
    print(f"  Phase A 6y: {len(curve_a6)} pts | Phase B 6y: {len(curve_b6)} pts")

    daily_a5 = daily_pnls_from_equity(curve_a5)
    daily_b5 = daily_pnls_from_equity(curve_b5)
    daily_a6 = daily_pnls_from_equity(curve_a6)
    daily_b6 = daily_pnls_from_equity(curve_b6)

    # Inject straddle into Phase B
    daily_b5_ov, n_inj5 = inject_straddle(daily_b5, rows_5y)
    rows_all = rows_5y + rows_2022
    daily_b6_ov, n_inj6 = inject_straddle(daily_b6, rows_all)

    print(f"\n  CPI injections: 5y={n_inj5} standalone | 6y={n_inj6} standalone")

    # --- Funded sim ---
    print("\n[4] Two-phase funded_sim (h=200, gap=24):")
    HAIRCUT = 200; GAP = 24
    a5s = phase_stats(daily_a5, HAIRCUT, GAP)
    a6s = phase_stats(daily_a6, HAIRCUT, GAP)
    b5_base = phase_stats(daily_b5, HAIRCUT, GAP)
    b5_ov = phase_stats(daily_b5_ov, HAIRCUT, GAP)
    b6_base = phase_stats(daily_b6, HAIRCUT, GAP)
    b6_ov = phase_stats(daily_b6_ov, HAIRCUT, GAP)

    eco_5y_base = pipeline_economics(a5s, b5_base)
    eco_5y_ov = pipeline_economics(a5s, b5_ov)
    eco_6y_base = pipeline_economics(a6s, b6_base)
    eco_6y_ov = pipeline_economics(a6s, b6_ov)

    print("\n  Results (h=200, gap=24):")
    print(f"  {'Scenario':<30} {'$/mo':>7} {'sust':>8} {'A-pass':>7} {'B-bust':>7}")
    print(f"  {'-'*55}")
    print(f"  {'5y base (ref B93 gap=24)':<30} ${eco_5y_base['$/mo']:>6.0f} {eco_5y_base['sust']:>7.2f}x {a5s['c_passes']:>7} {b5_base['x_busts']:>7}")
    print(f"  {'5y + straddle (1-min ov)':<30} ${eco_5y_ov['$/mo']:>6.0f} {eco_5y_ov['sust']:>7.2f}x {a5s['c_passes']:>7} {b5_ov['x_busts']:>7}")
    print(f"  {'6y base (no straddle)':<30} ${eco_6y_base['$/mo']:>6.0f} {eco_6y_base['sust']:>7.2f}x {a6s['c_passes']:>7} {b6_base['x_busts']:>7}")
    print(f"  {'6y + straddle (w/2022)':<30} ${eco_6y_ov['$/mo']:>6.0f} {eco_6y_ov['sust']:>7.2f}x {a6s['c_passes']:>7} {b6_ov['x_busts']:>7}")

    print("\n  " + "=" * 55)
    print("  HOLDOUT VERDICT:")
    print(f"  2022 oracle:  PF={pf_2022:.2f}  WR={wr_2022:.1f}%  n={len(traded)}")
    if pf_2022 >= 3.0:
        print(f"  Oracle gate: PASS (PF {pf_2022:.2f} >= 3.0)")
    else:
        print(f"  Oracle gate: WARN (PF {pf_2022:.2f} < 3.0)")

    d_sust = eco_6y_ov["sust"] - eco_6y_base["sust"]
    d_mo = eco_6y_ov["$/mo"] - eco_6y_base["$/mo"]
    print(f"  6y overlay delta: sust {d_sust:+.2f}x | $/mo {d_mo:+.0f}")

    if pf_2022 >= 2.0 and eco_6y_ov["sust"] >= eco_6y_base["sust"]:
        verdict = "CONFIRMED -- 2022 holdout supports CPI straddle deployment"
    elif pf_2022 >= 1.0:
        verdict = "WEAK -- 2022 oracle positive but mixed funded metrics"
    else:
        verdict = "FAIL -- 2022 holdout negative, deployment risk flagged"
    print(f"\n  FINAL: {verdict}")


if __name__ == "__main__":
    main()
