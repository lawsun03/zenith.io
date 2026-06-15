"""
B43 pipeline: ORB late-session cutoff effect on Phase B standalone and two-phase pipeline.

Phase A: equity_b42/deployed_r1p0_{year}.csv  (B42 deployed: 42 passes/5y, $568 reset)
Phase B (test):  equity_b43/window60_r0p75_{year}.csv  /  window90_r0p75_{year}.csv
Phase B (ref):   equity_b21/orb_reentry_r0p75_{year}.csv  (13 busts / 14 accts / $3131/acct)

B43 success criteria (Phase B standalone): bust rate decreases OR net_per_acct increases
"""
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

YEARS = ["2021", "2023", "2024", "2025", "2026"]
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B21_BENCHMARK = {"net_per_month": 497.0, "sustainability": 2.62}
B31_BENCHMARK = {"net_per_month": 508.0, "sustainability": 2.85}
B42_BENCHMARK = {"net_per_month": 549.0, "sustainability": 3.23}


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
        seg_start = seg[0][1]
        for ts, eq in seg:
            curve.append((ts, eq - seg_start + offset))
        offset = curve[-1][1]
    return curve


def phase_stats(equity_dir: Path, prefix: str, label: str) -> dict:
    curve = stitch(equity_dir, prefix)
    if not curve:
        return {}
    daily = daily_pnls_from_equity(curve)
    trading_days = len(daily)
    c = simulate_combines(daily, haircut=HAIRCUT)
    x = simulate_xfa_chain(daily, haircut=HAIRCUT)
    avg_days_per_attempt = (
        Decimal(str(trading_days)) / Decimal(str(c["attempts"]))
        if c["attempts"] else Decimal("0")
    )
    attempts_per_funded = (
        Decimal(str(c["attempts"])) / Decimal(str(c["passes"]))
        if c["passes"] else Decimal("inf")
    )
    combine_days_per_funded = attempts_per_funded * avg_days_per_attempt
    reset_fee_per_funded = attempts_per_funded * COMBINE_RESET_FEE
    avg_funded_days = (
        Decimal(str(trading_days)) / Decimal(str(x["accounts"]))
        if x["accounts"] else Decimal("0")
    )
    net_per_funded = (
        Decimal(str(x["net_payouts"])) / Decimal(str(x["accounts"]))
        if x["accounts"] else Decimal("0")
    )
    return {
        "label": label,
        "c_passes": c["passes"], "c_attempts": c["attempts"],
        "c_days_per_funded": float(combine_days_per_funded),
        "c_reset_fee": float(reset_fee_per_funded),
        "x_accounts": x["accounts"], "x_busts": x["busts"],
        "x_net_per_account": float(net_per_funded),
        "x_avg_days": float(avg_funded_days),
    }


def pipeline_economics(a: dict, b: dict) -> dict:
    reset_cost = Decimal(str(a["c_reset_fee"]))
    xfa_net = Decimal(str(b["x_net_per_account"]))
    net_per_cycle = xfa_net - reset_cost
    combine_days = Decimal(str(a["c_days_per_funded"]))
    funded_days = Decimal(str(b["x_avg_days"]))
    cycle_days = combine_days + funded_days
    net_per_month = (net_per_cycle * TRADING_DAYS_PER_MONTH / cycle_days
                     if cycle_days else Decimal("0"))
    sustainability = (
        Decimal(str(a["c_passes"])) / Decimal(str(b["x_busts"]))
        if b.get("x_busts") else Decimal("inf")
    )
    return {
        "reset_cost": float(reset_cost), "xfa_net": float(xfa_net),
        "net_per_cycle": float(net_per_cycle), "cycle_days": float(cycle_days),
        "net_per_month": float(net_per_month), "sustainability": float(sustainability),
    }


def main() -> None:
    eq_b21 = _REPO_ROOT / "research" / "equity_b21"
    eq_b42 = _REPO_ROOT / "research" / "equity_b42"
    eq_b43 = _REPO_ROOT / "research" / "equity_b43"

    print("\n=== PHASE B STANDALONE (5y excl 2022, haircut $200) ===")
    print(f"{'Config':<30} {'Accts':>5} {'Busts':>5} {'$/acct':>7} {'d/acct':>6} {'Passes':>6}")
    print("-" * 60)

    configs_b = [
        (eq_b21, "orb_reentry_r0p75", "B21 ref (old baseline)"),
        (eq_b43, "window00_r0p75",    "B43 w=0 (parity baseline)"),
        (eq_b43, "window60_r0p75",    "B43 w=60"),
        (eq_b43, "window90_r0p75",    "B43 w=90"),
    ]
    stats_b = {}
    for eq_dir, prefix, label in configs_b:
        s = phase_stats(eq_dir, prefix, label)
        if not s:
            continue
        stats_b[label] = s
        print(f"  {label:<28} {s['x_accounts']:5d} {s['x_busts']:5d} "
              f"${s['x_net_per_account']:>6.0f} {s['x_avg_days']:6.1f}d {s['c_passes']:5d}")

    print("\n=== PHASE A REFERENCE (B42 deployed r=1.0%, 5y excl 2022) ===")
    s_a = phase_stats(eq_b42, "deployed_r1p0", "B42 Phase A deployed")
    if s_a:
        print(f"  {s_a['label']}: {s_a['c_passes']} passes / {s_a['c_attempts']} attempts | "
              f"{s_a['c_days_per_funded']:.1f}d/funded | ${s_a['c_reset_fee']:.0f}/funded")

    print("\n=== TWO-PHASE PIPELINE: B42 Phase A -> various Phase B ===")
    if s_a:
        print(f"{'Phase B':<30} {'Reset$':>7} {'XFA$/ac':>7} {'$/cyc':>7} {'cyc d':>6} {'$/mo':>6} {'sust':>6}")
        print("-" * 75)
        for label, s_b in stats_b.items():
            if not s_b:
                continue
            econ = pipeline_economics(s_a, s_b)
            beats = ""
            if (econ["net_per_month"] >= B42_BENCHMARK["net_per_month"]
                    and econ["sustainability"] >= B42_BENCHMARK["sustainability"]):
                beats = " *** BEATS B42"
            elif (econ["net_per_month"] >= B31_BENCHMARK["net_per_month"]
                  and econ["sustainability"] >= B31_BENCHMARK["sustainability"]):
                beats = " ** BEATS B31"
            elif (econ["net_per_month"] >= B21_BENCHMARK["net_per_month"]
                  and econ["sustainability"] >= B21_BENCHMARK["sustainability"]):
                beats = " * BEATS B21"
            print(f"  {label:<28} ${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>5.0f}  "
                  f"${econ['net_per_cycle']:>5.0f}  {econ['cycle_days']:>5.1f}d  "
                  f"${econ['net_per_month']:>5.0f}  {econ['sustainability']:>5.2f}x{beats}")


if __name__ == "__main__":
    main()
