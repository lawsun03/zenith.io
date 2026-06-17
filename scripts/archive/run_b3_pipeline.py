"""
B3 driver: two-phase pipeline policy simulation.

Models a single trader cycling: Combine (Phase A config) -> XFA Funded (Phase B config).
Uses existing equity CSVs from B1. All analysis is purely from sequential simulation
of existing equity series — no Monte Carlo, no resampling.

    python scripts/run_b3_pipeline.py

Output: pipeline sustainability table + per-cycle economics for all A->B combinations.
"""
from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.funded_sim import (
    daily_pnls_from_equity,
    simulate_combines,
    simulate_xfa_chain,
)

EQUITY_DIR = _REPO_ROOT / "research" / "equity_b1"
YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")   # $ per combine attempt
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

# Phase A (Combine) configs to evaluate
PHASE_A_CONFIGS = {
    "iFVG r1.25":   ("control", "1p25"),
    "ORB r0.75":    ("orb",     "0p75"),
}

# Phase B (Funded/XFA) configs to evaluate
PHASE_B_CONFIGS = {
    "ORB r0.5":     ("orb", "0p5"),
    "ORB r0.75":    ("orb", "0p75"),
    "ORB r1.0":     ("orb", "1"),
    "ORB r1.25":    ("orb", "1p25"),
}


def stitch(variant: str, risk_str: str) -> list[tuple[datetime, Decimal]]:
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in YEARS:
        tag = f"{variant}_r{risk_str}_{year}"
        p = EQUITY_DIR / f"{tag}.csv"
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


def phase_stats(variant: str, risk_str: str) -> dict:
    """Return combine and xfa stats for this config at HAIRCUT."""
    curve = stitch(variant, risk_str)
    daily = daily_pnls_from_equity(curve)
    trading_days = len(daily)
    c = simulate_combines(daily, haircut=HAIRCUT)
    x = simulate_xfa_chain(daily, haircut=HAIRCUT)
    # Average days per attempt (all attempts, not just passing ones)
    avg_days_per_attempt = (
        Decimal(str(trading_days)) / Decimal(str(c["attempts"]))
        if c["attempts"] else Decimal("0")
    )
    # Attempts needed per funded account (geometric mean approximation)
    attempts_per_funded = (
        Decimal(str(c["attempts"])) / Decimal(str(c["passes"]))
        if c["passes"] else Decimal("inf")
    )
    # Combine time (trading days) to get one funded account
    combine_days_per_funded = attempts_per_funded * avg_days_per_attempt
    # Reset fee per funded account
    reset_fee_per_funded = attempts_per_funded * COMBINE_RESET_FEE
    # XFA: average account duration and net payout per account
    avg_funded_days = (
        Decimal(str(trading_days)) / Decimal(str(x["accounts"]))
        if x["accounts"] else Decimal("0")
    )
    net_per_funded = (
        x["net_payouts"] / Decimal(str(x["accounts"]))
        if x["accounts"] else Decimal("0")
    )
    # Pipeline sustainability: combine.passes vs xfa.busts over same period
    sustainability = (
        Decimal(str(c["passes"])) / Decimal(str(x["busts"]))
        if x["busts"] else Decimal("inf")
    )
    return {
        "variant": variant, "risk_str": risk_str,
        "trading_days": trading_days,
        # Combine stats
        "c_attempts": c["attempts"], "c_passes": c["passes"],
        "c_avg_days": float(avg_days_per_attempt),
        "c_attempts_per_funded": float(attempts_per_funded),
        "c_days_per_funded": float(combine_days_per_funded),
        "c_reset_fee": float(reset_fee_per_funded),
        # XFA stats
        "x_accounts": x["accounts"], "x_busts": x["busts"],
        "x_net_payouts": float(x["net_payouts"]),
        "x_net_per_account": float(net_per_funded),
        "x_avg_days": float(avg_funded_days),
        # Sustainability: combine passes / xfa busts (>1 = self-sustaining)
        "sustainability": float(sustainability),
    }


def pipeline_economics(a: dict, b: dict) -> dict:
    """Compute per-cycle and monthly economics for A->B coupling."""
    # Cost: Phase A combine fees per funded account
    reset_cost = Decimal(str(a["c_reset_fee"]))
    # Revenue: Phase B net payout per funded account
    xfa_net = Decimal(str(b["x_net_per_account"]))
    # Net per cycle
    net_per_cycle = xfa_net - reset_cost
    # Time per cycle (trading days): combine time + funded duration
    combine_days = Decimal(str(a["c_days_per_funded"]))
    funded_days = Decimal(str(b["x_avg_days"]))
    cycle_days = combine_days + funded_days
    # Net per trading day -> per month
    net_per_day = net_per_cycle / cycle_days if cycle_days else Decimal("0")
    net_per_month = net_per_day * TRADING_DAYS_PER_MONTH
    # Pipeline sustainability: A combine passes vs B xfa busts (same 5y window)
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
    print(f"B3 Pipeline Analysis  (haircut=${HAIRCUT}, reset_fee=${COMBINE_RESET_FEE}/attempt)")
    print(f"Non-holdout years: {', '.join(YEARS)}\n")

    # Compute stats for each config
    a_stats: dict[str, dict] = {}
    b_stats: dict[str, dict] = {}
    configs_loaded: set[tuple[str, str]] = set()

    all_configs = {**PHASE_A_CONFIGS, **PHASE_B_CONFIGS}
    for label, (variant, risk_str) in all_configs.items():
        key = (variant, risk_str)
        if key not in configs_loaded:
            configs_loaded.add(key)
            print(f"Loading {label} ({variant} r{risk_str})...", end=" ", flush=True)
            stats = phase_stats(variant, risk_str)
            print(
                f"combine {stats['c_passes']}/{stats['c_attempts']} "
                f"(avg {stats['c_avg_days']:.1f}d) | "
                f"xfa {stats['x_busts']}/{stats['x_accounts']} "
                f"net ${stats['x_net_payouts']:.0f}"
            )
            a_stats[label] = stats
            b_stats[label] = stats

    print()
    print("=" * 80)
    print("PER-PHASE STATS (Phase A = Combine configs, Phase B = XFA configs)")
    print("=" * 80)
    print(f"{'Config':<15} {'A: pass/att':<13} {'A: days/acct':<14} {'A: fee/acct':<12} "
          f"{'B: net/acct':<12} {'B: days/acct':<13} {'B: sust':<8}")
    print("-" * 80)
    for label in all_configs:
        s = a_stats[label]
        print(f"{label:<15} {s['c_passes']}/{s['c_attempts']:<10} "
              f"{s['c_days_per_funded']:>8.1f}d      "
              f"${s['c_reset_fee']:>7.0f}      "
              f"${s['x_net_per_account']:>7.0f}      "
              f"{s['x_avg_days']:>7.1f}d      "
              f"{s['sustainability']:>5.2f}x")

    print()
    print("=" * 80)
    print("TWO-PHASE PIPELINE: A (Combine) -> B (Funded)  -- all at h200, $150/attempt")
    print("Sustainability = A.combine_passes / B.xfa_busts (>1 = self-sustaining)")
    print("=" * 80)
    print(f"{'Phase A -> Phase B':<30} {'Reset$':<8} {'XFA$':<8} {'Net/cycle':<11} "
          f"{'Cycle days':<11} {'Net/mo':<9} {'Sustain'}")
    print("-" * 80)

    rows = []
    for a_label, (a_variant, a_risk) in PHASE_A_CONFIGS.items():
        a_key = a_label
        for b_label, (b_variant, b_risk) in PHASE_B_CONFIGS.items():
            b_key = b_label
            econ = pipeline_economics(a_stats[a_key], b_stats[b_key])
            combo = f"{a_label} -> {b_label}"
            rows.append((econ["net_per_month"], combo, econ))

    # Sort by net/month descending
    rows.sort(key=lambda r: -r[0])
    for _, combo, econ in rows:
        flag = " *** BEST" if econ == rows[0][2] else ""
        print(f"{combo:<30} ${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>6.0f}  "
              f"${econ['net_per_cycle']:>8.0f}   {econ['cycle_days']:>7.1f}d   "
              f"${econ['net_per_month']:>6.0f}   {econ['sustainability']:>5.2f}x{flag}")

    print()
    print("=" * 80)
    print("SINGLE-PHASE BENCHMARKS (A = B, same config for both)")
    print("=" * 80)
    print(f"{'Config':<15} {'Reset$':<8} {'XFA$':<8} {'Net/cycle':<11} "
          f"{'Cycle days':<11} {'Net/mo':<9} {'Sustain'}")
    print("-" * 80)
    single_rows = []
    for label in list(PHASE_A_CONFIGS) + [l for l in PHASE_B_CONFIGS if l not in PHASE_A_CONFIGS]:
        if label in a_stats:
            econ = pipeline_economics(a_stats[label], b_stats[label])
            single_rows.append((econ["net_per_month"], label, econ))
    single_rows.sort(key=lambda r: -r[0])
    for _, label, econ in single_rows:
        print(f"{label:<15}       ${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>6.0f}  "
              f"${econ['net_per_cycle']:>8.0f}   {econ['cycle_days']:>7.1f}d   "
              f"${econ['net_per_month']:>6.0f}   {econ['sustainability']:>5.2f}x")

    print()
    print("Definitions:")
    print("  Reset$/acct   = combine_attempts_per_funded * $150")
    print("  XFA$/acct     = total_xfa_net / total_accounts")
    print("  Net/cycle     = XFA$/acct - Reset$/acct")
    print("  Cycle days    = combine_days_per_funded + xfa_days_per_account (trading days)")
    print("  Net/mo        = Net/cycle * 21 / cycle_days (trading days -> calendar months)")
    print("  Sustainability = A.combine_passes / B.xfa_busts over same 5y window")


if __name__ == "__main__":
    main()
