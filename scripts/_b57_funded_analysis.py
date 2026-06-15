"""B57 funded-pipeline analysis: r_multiple sensitivity."""
from __future__ import annotations

import csv
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

YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")


def stitch(equity_dir: Path, prefix: str) -> list[tuple[datetime, Decimal]]:
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in YEARS:
        p = equity_dir / f"{prefix}_{year}.csv"
        if not p.exists():
            print(f"  WARNING: missing {p}", file=sys.stderr)
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


def phase_stats(equity_dir: Path, prefix: str) -> dict:
    curve = stitch(equity_dir, prefix)
    if not curve:
        return {}
    daily = daily_pnls_from_equity(curve)
    trading_days = len(daily)
    c = simulate_combines(daily, haircut=HAIRCUT)
    x = simulate_xfa_chain(daily, haircut=HAIRCUT)
    avg_days = Decimal(str(trading_days)) / Decimal(str(c["attempts"])) if c["attempts"] else Decimal("0")
    att_per_funded = Decimal(str(c["attempts"])) / Decimal(str(c["passes"])) if c["passes"] else Decimal("inf")
    reset_fee = att_per_funded * Decimal("150")
    avg_funded_days = Decimal(str(trading_days)) / Decimal(str(x["accounts"])) if x["accounts"] else Decimal("0")
    net_per_funded = x["net_payouts"] / Decimal(str(x["accounts"])) if x["accounts"] else Decimal("0")
    return {
        "c_attempts": c["attempts"], "c_passes": c["passes"],
        "c_avg_days": float(avg_days),
        "c_att_per_funded": float(att_per_funded),
        "c_days_per_funded": float(att_per_funded * avg_days),
        "c_reset_fee": float(reset_fee),
        "x_accounts": x["accounts"], "x_busts": x["busts"],
        "x_net_payouts": float(x["net_payouts"]),
        "x_net_per_account": float(net_per_funded),
        "x_avg_days": float(avg_funded_days),
    }


def pipeline_economics(a: dict, b: dict) -> dict:
    reset_cost = Decimal(str(a["c_reset_fee"]))
    xfa_net = Decimal(str(b["x_net_per_account"]))
    net_per_cycle = xfa_net - reset_cost
    cycle_days = Decimal(str(a["c_days_per_funded"])) + Decimal(str(b["x_avg_days"]))
    net_per_day = net_per_cycle / cycle_days if cycle_days else Decimal("0")
    net_per_month = net_per_day * TRADING_DAYS_PER_MONTH
    sustainability = (
        Decimal(str(a["c_passes"])) / Decimal(str(b["x_busts"]))
        if b["x_busts"] else Decimal("inf")
    )
    return {
        "reset_cost": float(reset_cost),
        "xfa_net": float(xfa_net),
        "net_per_cycle": float(net_per_cycle),
        "cycle_days": float(cycle_days),
        "net_per_month": float(net_per_month),
        "sustainability": float(sustainability),
    }


def main() -> None:
    eq_b57 = _REPO_ROOT / "research" / "equity_b57"
    eq_b42 = _REPO_ROOT / "research" / "equity_b42"
    eq_b21 = _REPO_ROOT / "research" / "equity_b21"

    # Phase B (ORB-reentry r=0.75 — same across all variants)
    print("Loading Phase B (ORB-reentry r=0.75)...")
    sb = phase_stats(eq_b21, "orb_reentry_r0p75")
    if not sb:
        print("ERROR: missing Phase B equity", file=sys.stderr)
        sys.exit(1)
    print(f"  Phase B: xfa busts={sb['x_busts']}/{sb['x_accounts']} "
          f"net ${sb['x_net_payouts']:.0f} (${sb['x_net_per_account']:.0f}/acct, "
          f"{sb['x_avg_days']:.1f}d/acct)")

    # Phase A variants
    variants = [
        ("B57 r=2.0", eq_b57, "r2p0"),
        ("B57 r=2.5", eq_b57, "r2p5"),
        ("B57 r=3.0", eq_b57, "r3p0"),
        ("B42 r=3.5 (baseline)", eq_b42, "deployed_r1p0"),
    ]

    print("\n" + "=" * 90)
    print("B57 FUNDED PIPELINE: r_multiple sensitivity (Phase A -> ORB-reentry r=0.75 Phase B)")
    print("Baseline: B42 deployed r=3.5, $549/mo, sust=3.23x")
    print("=" * 90)
    hdr = f"{'Variant':<30} {'A:pass/att':>12} {'Reset$':>7} {'XFA$':>7} {'Net/mo':>8} {'Sust':>8} {'CycleD':>8}"
    print(hdr)
    print("-" * 90)

    results = []
    for label, eq_dir, prefix in variants:
        sa = phase_stats(eq_dir, prefix)
        if not sa:
            print(f"  {label}: missing equity data")
            continue
        econ = pipeline_economics(sa, sb)
        results.append((label, sa, econ))
        flag = ""
        if econ["net_per_month"] >= 549 and econ["sustainability"] >= 3.23:
            flag = " *** BEATS B42"
        elif econ["net_per_month"] >= 500 and econ["sustainability"] >= 3.23:
            flag = " * beats sust>=3.23 + $/mo>=500"
        print(f"{label:<30} {sa['c_passes']}/{sa['c_attempts']:>3}        ${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>5.0f}  ${econ['net_per_month']:>6.0f}  {econ['sustainability']:>6.2f}x  {econ['cycle_days']:>6.1f}d{flag}")

    print("\nVERDICT:")
    baseline_mo = None
    baseline_sust = None
    for label, sa, econ in results:
        if "baseline" in label.lower():
            baseline_mo = econ["net_per_month"]
            baseline_sust = econ["sustainability"]
    for label, sa, econ in results:
        if "baseline" in label.lower():
            print(f"  {label}: ${econ['net_per_month']:.0f}/mo, sust {econ['sustainability']:.2f}x [baseline]")
        else:
            if baseline_mo and baseline_sust:
                mo_delta = econ["net_per_month"] - baseline_mo
                sust_delta = econ["sustainability"] - baseline_sust
                flag = "BETTER" if mo_delta > 0 and sust_delta > 0 else ("worse" if mo_delta < 0 and sust_delta < 0 else "mixed")
                print(f"  {label}: ${econ['net_per_month']:.0f}/mo, sust {econ['sustainability']:.2f}x "
                      f"({'+' if mo_delta >= 0 else ''}{mo_delta:.0f}$/mo, {'+' if sust_delta >= 0 else ''}{sust_delta:.2f}x sust) [{flag}]")
    print()


if __name__ == "__main__":
    main()
