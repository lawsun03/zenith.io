"""
B41 driver: two-phase pipeline — Combined-engine Combine (Phase A) -> ORB-reentry Funded (Phase B).

Hypothesis: B40 showed engine=combined at B21 research baseline (named sessions, ifvg_edge,
no MNQ overrides) gives 2x more Phase A combine passes than ifvg-only (10/61 vs 5/61, PF 1.02
vs 0.85). B21 used ifvg-only Phase A (5/61 per period, ~34 passes/5y).
If combined Phase A gives ~10/61 per period, that's ~68 passes over 5y — doubling sust and
improving $/month substantially.

Phase A equity: equity_b41/combined_r1p25_{year}.csv (combined engine, research baseline)
Phase B equity: equity_b21/orb_reentry_r0p75_{year}.csv (unchanged from B21)

Success criteria (vs B31 winner: $508/mo, sust 2.85x):
  - PRIMARY: $/mo >= $508 AND sust >= 2.85x (beats B31 on BOTH metrics)
  - SECONDARY: $/mo >= $497 AND sust >= 2.62x (beats B21)

Usage:
    python scripts/run_b41_pipeline.py
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

EQUITY_DIR_B1  = _REPO_ROOT / "research" / "equity_b1"
EQUITY_DIR_B21 = _REPO_ROOT / "research" / "equity_b21"
EQUITY_DIR_B31 = _REPO_ROOT / "research" / "equity_b31"
EQUITY_DIR_B41 = _REPO_ROOT / "research" / "equity_b41"
YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B21_BENCHMARK = {"net_per_month": 497.0, "sustainability": 2.62}
B31_BENCHMARK = {"net_per_month": 508.0, "sustainability": 2.85}

PHASE_A_CONFIGS = {
    "combined r1.25 (B41)": (EQUITY_DIR_B41, "combined", "r1p25"),
    # Reference Phase A configs for comparison
    "iFVG r1.25 (B21 ref)": (EQUITY_DIR_B1, "control", "r1p25"),
    "iFVG r2.0 deployed (B31 ref)": (EQUITY_DIR_B31, "ifvg_r2p0", ""),
}

PHASE_B_CONFIGS = {
    "ORB-reentry r0.75 (B21)": (EQUITY_DIR_B21, "orb_reentry", "r0p75"),
}


def stitch(equity_dir: Path, variant: str, risk_str: str) -> list[tuple[datetime, Decimal]]:
    """Stitch per-year equity CSVs into a single continuous curve."""
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in YEARS:
        # Support both naming conventions (with/without explicit 'r' prefix separator)
        candidates = [
            equity_dir / f"{variant}_{risk_str}_{year}.csv",
            equity_dir / f"{variant}_r{risk_str}_{year}.csv",
            equity_dir / f"{variant}_{year}.csv",  # no risk suffix (e.g. ifvg_r2p0_2021.csv)
        ]
        p = next((c for c in candidates if c.exists()), None)
        if p is None:
            print(f"  WARNING: missing equity CSV for {variant} {risk_str} {year} "
                  f"(tried: {[str(c) for c in candidates]})", file=sys.stderr)
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


def phase_stats(equity_dir: Path, variant: str, risk_str: str) -> dict:
    """Return combine and xfa stats for this config at HAIRCUT."""
    curve = stitch(equity_dir, variant, risk_str)
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
        x["net_payouts"] / Decimal(str(x["accounts"]))
        if x["accounts"] else Decimal("0")
    )
    sustainability = (
        Decimal(str(c["passes"])) / Decimal(str(x["busts"]))
        if x["busts"] else Decimal("inf")
    )
    return {
        "variant": variant, "risk_str": risk_str,
        "trading_days": trading_days,
        "c_attempts": c["attempts"], "c_passes": c["passes"],
        "c_avg_days": float(avg_days_per_attempt),
        "c_attempts_per_funded": float(attempts_per_funded),
        "c_days_per_funded": float(combine_days_per_funded),
        "c_reset_fee": float(reset_fee_per_funded),
        "x_accounts": x["accounts"], "x_busts": x["busts"],
        "x_net_payouts": float(x["net_payouts"]),
        "x_net_per_account": float(net_per_funded),
        "x_avg_days": float(avg_funded_days),
        "sustainability": float(sustainability),
    }


def pipeline_economics(a: dict, b: dict) -> dict:
    """Compute per-cycle and monthly economics for A->B coupling."""
    reset_cost = Decimal(str(a["c_reset_fee"]))
    xfa_net = Decimal(str(b["x_net_per_account"]))
    net_per_cycle = xfa_net - reset_cost
    combine_days = Decimal(str(a["c_days_per_funded"]))
    funded_days = Decimal(str(b["x_avg_days"]))
    cycle_days = combine_days + funded_days
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
    print("B41 Pipeline Analysis: Combined-engine Combine -> ORB-reentry Funded")
    print(f"(haircut=${HAIRCUT}, reset_fee=${COMBINE_RESET_FEE}/attempt, years={YEARS})")
    print(f"Success criteria vs B21: $/mo >= {B21_BENCHMARK['net_per_month']} AND "
          f"sust >= {B21_BENCHMARK['sustainability']}")
    print(f"Success criteria vs B31: $/mo >= {B31_BENCHMARK['net_per_month']} AND "
          f"sust >= {B31_BENCHMARK['sustainability']}\n")

    stats: dict[str, dict] = {}

    print("=== Phase A standalone stats ===")
    for label, (equity_dir, variant, risk_str) in PHASE_A_CONFIGS.items():
        print(f"  Loading {label}...", end=" ", flush=True)
        s = phase_stats(equity_dir, variant, risk_str)
        if not s:
            print("MISSING — skipped")
            continue
        stats[label] = s
        print(
            f"combine {s['c_passes']}/{s['c_attempts']} "
            f"(avg {s['c_avg_days']:.1f}d/attempt, {s['c_days_per_funded']:.1f}d/funded, "
            f"${s['c_reset_fee']:.0f}/funded)"
        )

    print("\n=== Phase B standalone stats ===")
    for label, (equity_dir, variant, risk_str) in PHASE_B_CONFIGS.items():
        print(f"  Loading {label}...", end=" ", flush=True)
        s = phase_stats(equity_dir, variant, risk_str)
        if not s:
            print("MISSING — skipped")
            continue
        stats[label] = s
        print(
            f"xfa busts={s['x_busts']}/{s['x_accounts']} "
            f"net ${s['x_net_payouts']:.0f} "
            f"(${s['x_net_per_account']:.0f}/acct, {s['x_avg_days']:.1f}d/acct) "
            f"sust={s['sustainability']:.2f}x"
        )

    print()
    print("=" * 100)
    print("B41 TWO-PHASE PIPELINE MATRIX")
    print("Sustainability = Phase_A.combine_passes / Phase_B.xfa_busts (>1 = self-sustaining)")
    print("=" * 100)
    hdr = f"{'Phase A -> Phase B':<50} {'Reset$':>7} {'XFA$':>7} {'Net/cyc':>9} {'Cycle d':>9} {'Net/mo':>8} {'Sust':>8}"
    print(hdr)
    print("-" * 100)

    b_label = "ORB-reentry r0.75 (B21)"
    if b_label not in stats:
        print(f"ERROR: Phase B '{b_label}' not loaded — aborting.")
        return
    b_s = stats[b_label]

    rows = []
    for a_label in PHASE_A_CONFIGS:
        if a_label not in stats:
            continue
        a_s = stats[a_label]
        econ = pipeline_economics(a_s, b_s)
        combo = f"{a_label} -> {b_label}"
        rows.append((econ["net_per_month"], combo, econ, a_label))

    rows.sort(key=lambda r: -r[0])
    for _, combo, econ, a_label in rows:
        beats_b31 = (econ["net_per_month"] >= B31_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B31_BENCHMARK["sustainability"])
        beats_b21 = (econ["net_per_month"] >= B21_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B21_BENCHMARK["sustainability"])
        flag = ""
        if beats_b31:
            flag = " *** BEATS B31"
        elif beats_b21:
            flag = " * BEATS B21"
        elif "B21 ref" in a_label:
            flag = " [B21 ref]"
        elif "B31 ref" in a_label:
            flag = " [B31 ref]"
        print(f"{combo:<50} ${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>5.0f}  "
              f"${econ['net_per_cycle']:>7.0f}  {econ['cycle_days']:>7.1f}d  "
              f"${econ['net_per_month']:>6.0f}  {econ['sustainability']:>6.2f}x{flag}")

    print()
    print("=" * 100)
    print("VERDICT")
    print("=" * 100)
    for _, combo, econ, a_label in rows:
        if "ref" in a_label:
            continue
        beats_b31 = (econ["net_per_month"] >= B31_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B31_BENCHMARK["sustainability"])
        beats_b21 = (econ["net_per_month"] >= B21_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B21_BENCHMARK["sustainability"])
        if beats_b31:
            verdict = f"CANDIDATE — beats B31 (${B31_BENCHMARK['net_per_month']}/mo, {B31_BENCHMARK['sustainability']}x) on BOTH metrics"
        elif beats_b21:
            verdict = f"CANDIDATE — beats B21 (${B21_BENCHMARK['net_per_month']}/mo, {B21_BENCHMARK['sustainability']}x) but not B31"
        else:
            verdict = f"below B21 threshold (${B21_BENCHMARK['net_per_month']}/mo, {B21_BENCHMARK['sustainability']}x)"
        print(f"  {combo}: ${econ['net_per_month']:.0f}/mo, sust {econ['sustainability']:.2f}x — {verdict}")

    print()
    print("Definitions:")
    print("  Reset$/acct   = combine_attempts_per_funded * $150")
    print("  XFA$/acct     = total_xfa_net / total_accounts")
    print("  Net/cycle     = XFA$/acct - Reset$/acct")
    print("  Cycle days    = combine_days_per_funded + xfa_days_per_account")
    print("  Net/mo        = Net/cycle * 21 / cycle_days")
    print("  Sustainability = A.combine_passes / B.xfa_busts over same 5y window")
    print("  B21 baseline  = iFVG r1.25 Combine + ORB-reentry r0.75 Funded = $497/mo, sust 2.62x")
    print("  B31 winner    = iFVG r2.0 deployed Combine + ORB-reentry r0.75 Funded = $508/mo, sust 2.85x")


if __name__ == "__main__":
    main()
