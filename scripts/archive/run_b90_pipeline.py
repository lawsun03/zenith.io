"""B90 pipeline analysis: CPI-day mode switch vs standalone CPI straddle.

Compares (two-phase pipeline_economics, same formula as B82):
  (a) Mode switch: CPI days -> straddle only; non-CPI -> base engine (B42 Phase A)
  (b) Standalone: CPI straddle on CPI days only, $0 on all other days
  Baseline: B42 deployed (equity_b42/deployed_r1p0) as Phase A

Phase B (funded XFA): equity_b21/orb_reentry_r0p75 (fixed for all variants).

Method:
  - Loads per-year base equity CSVs (equity_b42/deployed_r1p0_{year}.csv)
  - Runs 1s oracle on CPI events in each year to get (date, r_value)
  - Replaces per-day P&L on CPI days with straddle P&L (r_val * RISK_PER_R)
  - Reconstructs year-by-year equity CSVs, stitches via same stitch() as B82
  - Runs phase_stats() + pipeline_economics() to compare vs B42/B57 benchmarks

Reference benchmarks (from committed pipeline runs):
  B42: $549/mo, sust 3.23x
  B57: $566/mo, sust 3.54x

Stop rule: if mode switch (a) is WORSE than B42 on BOTH $/mo AND sust -> REJECTED
"""
from __future__ import annotations

import csv
import importlib.util
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.funded_sim import (
    daily_pnls_from_equity,
    simulate_combines,
    simulate_xfa_chain,
)
from app.risk.flatten import trading_day_ct

# Import 1s oracle (not a package, load as module)
_oracle_spec = importlib.util.spec_from_file_location(
    "news_straddle_cpi_1s",
    _REPO_ROOT / "scripts" / "news_straddle_cpi_1s.py",
)
_oracle_mod = importlib.util.module_from_spec(_oracle_spec)
_oracle_spec.loader.exec_module(_oracle_mod)
oracle_simulate = _oracle_mod.simulate
oracle_load_1s = _oracle_mod.load_1s


# --- Constants ---
YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
EQUITY_DIR_B42 = _REPO_ROOT / "research" / "equity_b42"
EQUITY_DIR_B21 = _REPO_ROOT / "research" / "equity_b21"
EQUITY_DIR_B90 = _REPO_ROOT / "research" / "equity_b90"
BARS_1S = _REPO_ROOT / "bars" / "bars_NQ_1s_cpi_windows.csv"
NEWS_CSV = _REPO_ROOT / "data" / "news_events.csv"

BASELINE = Decimal("50000")
RISK_PER_R = Decimal("500")          # 1% risk on $50k = $500/R; matches base engine
COMBINE_RESET_FEE = Decimal("150")   # $150/combine attempt (Topstep MNQ)
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

ORACLE_OFFSET_TICKS = 60
ORACLE_TP_R = 3.0

B42_BENCHMARK = {"net_per_month": 549.0, "sustainability": 3.23}
B57_BENCHMARK = {"net_per_month": 566.0, "sustainability": 3.54}


# ---------------------------------------------------------------------------
# Equity CSV helpers + stitch (same pattern as run_b82_pipeline.py)
# ---------------------------------------------------------------------------

def load_equity_csv(path: Path) -> list[tuple[datetime, Decimal]]:
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append((datetime.fromisoformat(row["ts"]), Decimal(row["equity"])))
    return rows


def write_equity_csv(path: Path, curve: list[tuple[datetime, Decimal]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "equity"])
        for ts, eq in curve:
            w.writerow([ts.isoformat(), str(eq)])


def stitch(equity_dir: Path, prefix: str, years: list[str] = YEARS) -> list[tuple[datetime, Decimal]]:
    """Chain per-year equity CSVs; carry forward balance between years."""
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in years:
        p = equity_dir / f"{prefix}_{year}.csv"
        if not p.exists():
            print(f"  WARNING: missing {p}", file=sys.stderr)
            continue
        seg = load_equity_csv(p)
        if not seg:
            continue
        if curve:
            offset = curve[-1][1] - BASELINE
        curve.extend((ts, eq + offset) for ts, eq in seg)
    return curve


def phase_stats(equity_dir: Path, prefix: str, years: list[str] = YEARS) -> dict:
    """Compute combine + XFA stats for a stitched equity curve."""
    curve = stitch(equity_dir, prefix, years)
    if not curve:
        return {}
    daily = daily_pnls_from_equity(curve)
    trading_days = len(daily)
    c = simulate_combines(daily, haircut=HAIRCUT)
    x = simulate_xfa_chain(daily, haircut=HAIRCUT)
    avg_days = (Decimal(str(trading_days)) / Decimal(str(c["attempts"]))
                if c["attempts"] else Decimal("0"))
    attempts_per_funded = (Decimal(str(c["attempts"])) / Decimal(str(c["passes"]))
                           if c["passes"] else Decimal("inf"))
    combine_days = attempts_per_funded * avg_days
    reset_fee = attempts_per_funded * COMBINE_RESET_FEE
    avg_funded_days = (Decimal(str(trading_days)) / Decimal(str(x["accounts"]))
                       if x["accounts"] else Decimal("0"))
    net_per_funded = (x["net_payouts"] / Decimal(str(x["accounts"]))
                      if x["accounts"] else Decimal("0"))
    sustainability = (Decimal(str(c["passes"])) / Decimal(str(x["busts"]))
                      if x["busts"] else Decimal("inf"))
    return {
        "trading_days": trading_days,
        "c_attempts": c["attempts"], "c_passes": c["passes"], "c_busts": c["busts"],
        "c_avg_days": float(avg_days),
        "c_days_per_funded": float(combine_days),
        "c_reset_fee": float(reset_fee),
        "x_accounts": x["accounts"], "x_busts": x["busts"],
        "x_net_payouts": float(x["net_payouts"]),
        "x_net_per_account": float(net_per_funded),
        "x_avg_days": float(avg_funded_days),
        "sustainability": float(sustainability),
    }


def pipeline_economics(a: dict, b: dict) -> dict:
    """Compute two-phase pipeline $/mo and sust (same formula as B82)."""
    reset_cost = Decimal(str(a["c_reset_fee"]))
    xfa_net = Decimal(str(b["x_net_per_account"]))
    net_per_cycle = xfa_net - reset_cost
    cycle_days = Decimal(str(a["c_days_per_funded"])) + Decimal(str(b["x_avg_days"]))
    net_per_day = net_per_cycle / cycle_days if cycle_days else Decimal("0")
    net_per_month = net_per_day * TRADING_DAYS_PER_MONTH
    sustainability = (Decimal(str(a["c_passes"])) / Decimal(str(b["x_busts"]))
                      if b["x_busts"] else Decimal("inf"))
    return {
        "reset_cost": float(reset_cost),
        "xfa_net": float(xfa_net),
        "net_per_cycle": float(net_per_cycle),
        "cycle_days": float(cycle_days),
        "net_per_month": float(net_per_month),
        "sustainability": float(sustainability),
    }


# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------

def get_cpi_events(years: list[str]) -> list[datetime]:
    year_ints = {int(y) for y in years}
    events = []
    with open(NEWS_CSV, newline="") as f:
        for row in csv.DictReader(f):
            if row["event_type"] == "CPI":
                ts = datetime.fromisoformat(row["ts_utc"])
                if ts.year in year_ints:
                    events.append(ts)
    return sorted(events)


def run_oracle(cpi_events: list[datetime]) -> list[tuple[datetime, float]]:
    """Run 1s oracle per CPI event. Returns (event_ts, r_val) for traded events."""
    df = oracle_load_1s(str(BARS_1S))
    results = []
    for ts in cpi_events:
        result = oracle_simulate(df, ts, ORACLE_OFFSET_TICKS, ORACLE_TP_R)
        if result is None:
            print(f"    SKIP {ts.date()}: insufficient bar coverage")
            continue
        outcome, r_val = result
        results.append((ts, r_val))
        print(f"    {ts.date()}  {outcome:8s}  {r_val:+.2f}R")
    return results


# ---------------------------------------------------------------------------
# Build modified per-year equity curves
# ---------------------------------------------------------------------------

def build_modified_year_curves(
    base_path: Path,
    year: int,
    cpi_oracle: list[tuple[datetime, float]],
) -> tuple[list[tuple[datetime, Decimal]], list[tuple[datetime, Decimal]]]:
    """Build mode-switch and standalone equity curves for one year.

    Mode switch: replace per-day base P&L on CPI days with straddle P&L.
    Standalone:  straddle P&L on CPI days; $0 on all other days.

    Uses trading_day_ct (same as daily_pnls_from_equity) for date alignment.
    Starting equity is BASELINE for each year (same as base files).
    """
    base_curve = load_equity_csv(base_path)

    # CPI days for this year: trading_day_ct(event_ts) -> r_value
    year_cpi: dict = {}
    for ts, r in cpi_oracle:
        if ts.year == year:
            d = trading_day_ct(ts)
            year_cpi[d] = r
            print(f"      {year} CPI day {d}: {r:+.2f}R -> ${float(Decimal(str(r)) * RISK_PER_R):+.0f}")

    daily = daily_pnls_from_equity(base_curve)

    switch_pnls: list[tuple[datetime, Decimal]] = []
    standalone_pnls: list[tuple[datetime, Decimal]] = []

    for ts, base_pnl in daily:
        d = trading_day_ct(ts)
        if d in year_cpi:
            pnl = Decimal(str(year_cpi[d])) * RISK_PER_R
            switch_pnls.append((ts, pnl))
            standalone_pnls.append((ts, pnl))
        else:
            switch_pnls.append((ts, base_pnl))
            standalone_pnls.append((ts, Decimal("0")))

    start_ts = base_curve[0][0]  # preserve same starting timestamp as base

    def pnls_to_curve(pnls: list[tuple[datetime, Decimal]]) -> list[tuple[datetime, Decimal]]:
        curve: list[tuple[datetime, Decimal]] = [(start_ts, BASELINE)]
        eq = BASELINE
        for ts, pnl in pnls:
            eq += pnl
            curve.append((ts, eq))
        return curve

    return pnls_to_curve(switch_pnls), pnls_to_curve(standalone_pnls)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== B90: CPI-day mode switch vs standalone CPI straddle ===")
    print(f"Oracle: offset={ORACLE_OFFSET_TICKS}t, tp_r={ORACLE_TP_R}R, "
          f"RISK_PER_R=${RISK_PER_R}, HAIRCUT=${HAIRCUT}\n")

    # Step 1: Oracle
    print("Step 1: 1s oracle on CPI events...")
    cpi_events = get_cpi_events(YEARS)
    print(f"  Scope: {len(cpi_events)} CPI events across {', '.join(YEARS)}")
    cpi_oracle = run_oracle(cpi_events)

    oracle_r = [r for _, r in cpi_oracle]
    if not oracle_r:
        print("ERROR: no oracle events returned. Check bars file.")
        return
    wins = [r for r in oracle_r if r > 0]
    mean_r = sum(oracle_r) / len(oracle_r)
    total_r = sum(oracle_r)

    print(f"\n  Oracle summary: {len(wins)}/{len(oracle_r)} wins "
          f"({100*len(wins)/len(oracle_r):.0f}%)")
    print(f"  Mean: {mean_r:+.3f}R = ${mean_r*float(RISK_PER_R):+.0f}/event  "
          f"|  Total: {total_r:+.1f}R = ${total_r*float(RISK_PER_R):+,.0f} over 5y\n")

    # Step 2: Build modified per-year equity CSVs
    print("Step 2: Building modified per-year equity CSVs...")
    EQUITY_DIR_B90.mkdir(parents=True, exist_ok=True)

    for year in YEARS:
        base_path = EQUITY_DIR_B42 / f"deployed_r1p0_{year}.csv"
        if not base_path.exists():
            print(f"  WARNING: {base_path.name} missing — skipping {year}")
            continue

        base_curve = load_equity_csv(base_path)
        base_net = float(base_curve[-1][1] - base_curve[0][1])
        n_cpi = sum(1 for ts, _ in cpi_oracle if ts.year == int(year))

        switch_curve, standalone_curve = build_modified_year_curves(
            base_path, int(year), cpi_oracle
        )

        write_equity_csv(EQUITY_DIR_B90 / f"switch_r1p0_{year}.csv", switch_curve)
        write_equity_csv(EQUITY_DIR_B90 / f"standalone_cpi_{year}.csv", standalone_curve)

        switch_net = float(switch_curve[-1][1] - switch_curve[0][1])
        standalone_net = float(standalone_curve[-1][1] - standalone_curve[0][1])
        delta = switch_net - base_net
        print(f"  {year}: {n_cpi} CPI days | "
              f"base ${base_net:+,.0f} | switch ${switch_net:+,.0f} (d{delta:+,.0f}) | "
              f"standalone ${standalone_net:+,.0f}")

    print()

    # Step 3: Phase A stats
    print("Step 3: Phase A combine stats (all variants)...")
    s_a_base = phase_stats(EQUITY_DIR_B42, "deployed_r1p0")
    s_a_switch = phase_stats(EQUITY_DIR_B90, "switch_r1p0")
    s_a_standalone = phase_stats(EQUITY_DIR_B90, "standalone_cpi")

    for label, s in [
        ("Base B42",   s_a_base),
        ("Switch B90a", s_a_switch),
        ("Standl B90b", s_a_standalone),
    ]:
        if s:
            print(f"  {label}: {s['c_passes']}/{s['c_attempts']} passes "
                  f"({s['c_busts']} busts), {s['c_days_per_funded']:.1f}d/funded, "
                  f"${s['c_reset_fee']:.0f}/funded")
        else:
            print(f"  {label}: no data")
    print()

    # Step 4: Phase B (fixed ORB-reentry r0.75)
    print("Step 4: Phase B stats (ORB-reentry r0.75, fixed)...")
    s_b = phase_stats(EQUITY_DIR_B21, "orb_reentry_r0p75")
    if s_b:
        print(f"  Phase B: {s_b['x_accounts']} accts, {s_b['x_busts']} busts, "
              f"${s_b['x_net_per_account']:.0f}/acct, {s_b['x_avg_days']:.1f}d/acct")
    print()

    # Step 5: Pipeline economics
    print("=" * 70)
    print("TWO-PHASE PIPELINE ECONOMICS (Phase B = ORB-reentry r0.75, h$200)")
    print("=" * 70)

    econ: dict[str, dict | None] = {}
    for label, s_a in [
        ("Base B42",    s_a_base),
        ("Switch B90a", s_a_switch),
        ("Standl B90b", s_a_standalone),
    ]:
        if not s_a or s_a.get("c_passes", 0) == 0:
            print(f"  {label:<15}: NO PASSES")
            econ[label] = None
            continue
        e = pipeline_economics(s_a, s_b)
        econ[label] = e
        print(f"  {label:<15}: ${e['net_per_month']:>6.0f}/mo  "
              f"sust={e['sustainability']:>5.2f}x  "
              f"(cycle {e['cycle_days']:.0f}d, net/cycle ${e['net_per_cycle']:.0f})")

    # Step 6: Verdict
    print()
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)

    b42_npm = B42_BENCHMARK["net_per_month"]
    b42_sust = B42_BENCHMARK["sustainability"]

    e_switch = econ.get("Switch B90a")
    e_standalone = econ.get("Standl B90b")

    if e_switch is None:
        print("Mode switch B90a: 0 passes -> REJECTED (volume starvation on CPI days)")
    else:
        sw_npm = e_switch["net_per_month"]
        sw_sust = e_switch["sustainability"]
        d_npm = sw_npm - b42_npm
        d_sust = sw_sust - b42_sust
        print(f"Mode switch B90a vs B42: $/mo {sw_npm:.0f} ({d_npm:+.0f}), "
              f"sust {sw_sust:.2f}x ({d_sust:+.2f})")
        if sw_npm > b42_npm and sw_sust >= b42_sust:
            print("RESULT: CANDIDATE -- beats B42 on BOTH $/mo AND sust")
        elif sw_npm <= b42_npm and sw_sust <= b42_sust:
            print("RESULT: REJECTED -- stop rule (BOTH $/mo AND sust worse than B42)")
        else:
            npm_dir = "+" if d_npm >= 0 else "-"
            sust_dir = "+" if d_sust >= 0 else "-"
            print(f"RESULT: MIXED/NO-GO -- $/mo {npm_dir}, sust {sust_dir} "
                  f"-- does not clearly beat B42 on both")

    if e_standalone is not None:
        print(f"Standalone B90b: ${e_standalone['net_per_month']:.0f}/mo "
              f"sust={e_standalone['sustainability']:.2f}x (reference only)")
    else:
        pass_count = (s_a_standalone or {}).get("c_passes", 0)
        print(f"Standalone B90b: {pass_count} combine passes "
              "-- sparse volume, combine starvation (Lesson 2 expected)")

    # Per-year oracle breakdown
    print("\n--- Oracle per-year ---")
    yr_r: dict[int, list] = {}
    for ts, r in cpi_oracle:
        yr_r.setdefault(ts.year, []).append(r)
    for yr in sorted(yr_r):
        rs = yr_r[yr]
        print(f"  {yr}: {sum(1 for r in rs if r > 0)}/{len(rs)} wins  "
              f"total {sum(rs):+.1f}R = ${sum(rs)*float(RISK_PER_R):+,.0f}")


if __name__ == "__main__":
    main()
