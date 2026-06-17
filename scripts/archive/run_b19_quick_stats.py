"""Quick pipeline stats for B19/B20/B21 research session."""
from __future__ import annotations

import csv, sys
from decimal import Decimal
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from app.backtest.funded_sim import (
    daily_pnls_from_equity,
    simulate_combines,
    simulate_xfa_chain,
)
from scripts.run_b3_pipeline import phase_stats, pipeline_economics  # reuse B3 model

HAIRCUT = Decimal("200")
COMBINE_RESET = Decimal("150")


def load_flat(path: Path):
    rows = []
    with open(path) as f:
        for row in csv.DictReader(f):
            rows.append((datetime.fromisoformat(row["ts"]), Decimal(row["equity"])))
    return rows


def flat_stats(label: str, path: Path) -> dict:
    curve = load_flat(path)
    daily = daily_pnls_from_equity(curve)
    c = simulate_combines(daily, haircut=HAIRCUT)
    x = simulate_xfa_chain(daily, haircut=HAIRCUT)
    n_accts = x["accounts"] or 1
    n_busts = x["busts"] or 1
    net_per_acct = float(x["net_payouts"]) / n_accts
    avg_days = len(daily) / n_accts
    sust = float(c["passes"]) / float(x["busts"]) if x["busts"] > 0 else float("inf")
    # Combine speed
    attempts = c["attempts"] or 1
    passes = c["passes"] or 1
    avg_days_per_attempt = len(daily) / attempts
    attempts_per_funded = attempts / passes
    combine_days_per_funded = attempts_per_funded * avg_days_per_attempt
    reset_per_funded = attempts_per_funded * float(COMBINE_RESET)
    print(
        f"  {label}: passes={c['passes']}  busts={x['busts']}  net=${float(x['net_payouts']):.0f}"
        f"  net/acct=${net_per_acct:.0f}  avg_days={avg_days:.1f}  sust={sust:.3f}x"
    )
    return {
        "c_passes": c["passes"], "x_busts": x["busts"], "c_days_per_funded": combine_days_per_funded,
        "c_reset_fee": reset_per_funded, "x_net_per_account": net_per_acct, "x_avg_days": avg_days,
    }


def pipeline_econ(a: dict, b: dict) -> dict:
    rc = Decimal(str(a["c_reset_fee"]))
    xn = Decimal(str(b["x_net_per_account"]))
    net_cycle = xn - rc
    cd = Decimal(str(a["c_days_per_funded"]))
    fd = Decimal(str(b["x_avg_days"]))
    cycle = cd + fd
    npm = net_cycle / cycle * 21 if cycle else Decimal("0")
    sust = float(a["c_passes"]) / float(b["x_busts"]) if b["x_busts"] else float("inf")
    return {"net_per_month": float(npm), "sustainability": sust, "cycle_days": float(cycle)}


def main():
    print("=" * 72)
    print("FUNDED-PHASE STANDALONE STATS  (h200)")
    print("=" * 72)

    files = {
        "B14 ORB-reentry r1.0": _REPO / "research/equity_b14_reentry_r1p0.csv",
        "B14 ORB-baseline r1.0": _REPO / "research/equity_b14_baseline_r1p0.csv",
        "B15 LongOnly-iFVG r0.75": _REPO / "research/equity_b15_ifvg_longonly_r075.csv",
        "B15 LongOnly-iFVG r1.00": _REPO / "research/equity_b15_ifvg_longonly_r100.csv",
        "B15 LongOnly-iFVG r1.25": _REPO / "research/equity_b15_ifvg_longonly_r125.csv",
    }

    flat: dict[str, dict] = {}
    for label, path in files.items():
        if not path.exists():
            print(f"  MISSING: {path}")
            continue
        flat[label] = flat_stats(label, path)

    print()
    print("=" * 72)
    print("B3 iFVG COMBINE PHASE STATS  (from equity_b1/ stitched CSVs)")
    print("=" * 72)
    b3_a = phase_stats("control", "1p25")
    print(
        f"  iFVG r1.25 combine: passes={b3_a['c_passes']}/{b3_a['c_attempts']}"
        f"  days/funded={b3_a['c_days_per_funded']:.1f}  reset/funded=${b3_a['c_reset_fee']:.0f}"
    )

    print()
    print("=" * 72)
    print("TWO-PHASE PIPELINE: iFVG Combine -> ? Funded  (h200, $150/attempt)")
    print("vs B3 benchmark: iFVG -> ORB r1.0 = $393/mo, sust 1.26x")
    print("=" * 72)
    b3_orb_r1 = phase_stats("orb", "1")
    b3_ref = pipeline_econ(b3_a, b3_orb_r1)
    print(f"  B3 ref (iFVG -> ORB r1.0): ${b3_ref['net_per_month']:.0f}/mo  sust {b3_ref['sustainability']:.3f}x  cycle {b3_ref['cycle_days']:.1f}d")
    print()

    # B20: iFVG Combine -> LongOnly-iFVG Funded at r1.0
    if "B15 LongOnly-iFVG r1.00" in flat:
        b20 = pipeline_econ(b3_a, flat["B15 LongOnly-iFVG r1.00"])
        print(f"  B20 (iFVG -> LongOnly-iFVG r1.0): ${b20['net_per_month']:.0f}/mo  sust {b20['sustainability']:.3f}x  cycle {b20['cycle_days']:.1f}d")
    if "B15 LongOnly-iFVG r0.75" in flat:
        b20b = pipeline_econ(b3_a, flat["B15 LongOnly-iFVG r0.75"])
        print(f"  B20 (iFVG -> LongOnly-iFVG r0.75): ${b20b['net_per_month']:.0f}/mo  sust {b20b['sustainability']:.3f}x  cycle {b20b['cycle_days']:.1f}d")
    if "B15 LongOnly-iFVG r1.25" in flat:
        b20c = pipeline_econ(b3_a, flat["B15 LongOnly-iFVG r1.25"])
        print(f"  B20 (iFVG -> LongOnly-iFVG r1.25): ${b20c['net_per_month']:.0f}/mo  sust {b20c['sustainability']:.3f}x  cycle {b20c['cycle_days']:.1f}d")

    # B21: iFVG Combine -> ORB-reentry Funded at r1.0
    if "B14 ORB-reentry r1.0" in flat:
        b21 = pipeline_econ(b3_a, flat["B14 ORB-reentry r1.0"])
        print(f"  B21 (iFVG -> ORB-reentry r1.0): ${b21['net_per_month']:.0f}/mo  sust {b21['sustainability']:.3f}x  cycle {b21['cycle_days']:.1f}d")


if __name__ == "__main__":
    main()
