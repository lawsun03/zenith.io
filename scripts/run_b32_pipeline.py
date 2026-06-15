"""
B32 driver: ORB-reentry Phase B at r=0.5 — closes the lower end of the reentry risk ladder.

Hypothesis: ORB-reentry r=0.75 (B21/B31 optimum) has 13 funded busts (per-year) and
$3,131/account net. At r=0.5, smaller per-trade risk means smaller daily swings → harder
to breach MLL in a few bad days → fewer busts. Question: does high sust at r=0.5 compensate
for lower per-account net, or does pipeline-constrained throughput make $/mo too low?

Phase A equity: equity_b1/control_r1p25_{year}.csv (iFVG r1.25, same as B21)
               equity_b31/ifvg_r2p0_{year}.csv (iFVG r2.0 deployed, B31 winner)
Phase B equity: equity_b32/orb_reentry_r0p5_{year}.csv (ORB-reentry at r=0.5)
Reference B:    equity_b21/orb_reentry_r0p75_{year}.csv (B21/B31 winner, r=0.75)

Success criteria (vs B21: $497/mo, sust 2.62x AND vs B31: $508/mo, sust 2.85x):
  - Primary: sust improvement while $/month >= $300/mo
  - If sust >> 2.62x but $/month < $300: note as "over-conservative, not practically useful"
  - If sust < 2.62x: reject (r=0.5 is strictly worse than r=0.75)

Usage:
    python scripts/run_b32_pipeline.py
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
EQUITY_DIR_B32 = _REPO_ROOT / "research" / "equity_b32"
YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B21_BENCHMARK = {"net_per_month": 497.0, "sustainability": 2.62}
B31_BENCHMARK = {"net_per_month": 508.0, "sustainability": 2.85}

PHASE_A_CONFIGS = {
    "iFVG-edge r1.25 (B21 Phase A)": (EQUITY_DIR_B1, "control", "r1p25"),
    "iFVG r2.0 deployed (B31 Phase A)": (EQUITY_DIR_B31, "ifvg", "r2p0"),
}

PHASE_B_CONFIGS = {
    "ORB-reentry r0.5 (B32)": (EQUITY_DIR_B32, "orb_reentry", "r0p5"),
    "ORB-reentry r0.75 (B21 ref)": (EQUITY_DIR_B21, "orb_reentry", "r0p75"),
}


def stitch(equity_dir: Path, variant: str, risk_str: str) -> list[tuple[datetime, Decimal]]:
    """Stitch per-year equity CSVs into a single continuous curve."""
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in YEARS:
        candidates = [
            equity_dir / f"{variant}_{risk_str}_{year}.csv",
            equity_dir / f"{variant}_r{risk_str}_{year}.csv",
            equity_dir / f"{variant}_{risk_str[1:]}_{year}.csv" if risk_str.startswith("r") else None,
        ]
        p = next((c for c in candidates if c is not None and c.exists()), None)
        if p is None:
            print(f"  WARNING: missing equity CSV for {variant} {risk_str} {year} "
                  f"(tried: {[str(c) for c in candidates if c]})", file=sys.stderr)
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
    print("B32 Pipeline Analysis: ORB-reentry r=0.5 as Phase B (risk floor check)")
    print(f"(haircut=${HAIRCUT}, reset_fee=${COMBINE_RESET_FEE}/attempt, years={YEARS})")
    print(f"Success vs B21: sust >= 2.62x AND $/mo >= $300\n")

    stats: dict[str, dict] = {}
    for label, args in {**PHASE_A_CONFIGS, **PHASE_B_CONFIGS}.items():
        equity_dir, variant, risk_str = args
        print(f"Loading {label}...", end=" ", flush=True)
        s = phase_stats(equity_dir, variant, risk_str)
        if not s:
            print("MISSING — skipped")
            continue
        stats[label] = s
        print(
            f"combine {s['c_passes']}/{s['c_attempts']} "
            f"(avg {s['c_avg_days']:.1f}d/attempt, {s['c_days_per_funded']:.1f}d/funded) | "
            f"xfa busts={s['x_busts']}/{s['x_accounts']} "
            f"net ${s['x_net_payouts']:.0f} (${s['x_net_per_account']:.0f}/acct) "
            f"sust_standalone={s['sustainability']:.2f}x"
        )

    print()
    print("=" * 100)
    print("B32 TWO-PHASE PIPELINE MATRIX")
    print("Sustainability = A.combine_passes / B.xfa_busts (>1 = self-sustaining)")
    print(f"B21 benchmark: $497/mo, sust 2.62x | B31 winner: $508/mo, sust 2.85x")
    print("=" * 100)
    print(f"{'Phase A -> Phase B':<55} {'Reset$':<8} {'XFA$':<8} {'Net/cycle':<11} "
          f"{'Cycle d':<9} {'Net/mo':<9} {'Sust'}")
    print("-" * 100)

    rows = []
    for a_label, a_args in PHASE_A_CONFIGS.items():
        if a_label not in stats:
            continue
        a_s = stats[a_label]
        for b_label, b_args in PHASE_B_CONFIGS.items():
            if b_label not in stats:
                continue
            b_s = stats[b_label]
            econ = pipeline_economics(a_s, b_s)
            combo = f"{a_label} -> {b_label}"
            rows.append((econ["net_per_month"], combo, econ, a_label, b_label))

    rows.sort(key=lambda r: -r[0])
    for _, combo, econ, a_label, b_label in rows:
        beats_b21 = (econ["net_per_month"] >= B21_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B21_BENCHMARK["sustainability"])
        beats_b31 = (econ["net_per_month"] >= B31_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B31_BENCHMARK["sustainability"])
        flag = " BEATS B31" if beats_b31 else (" BEATS B21" if beats_b21 else "")
        is_ref = "r0.75 (B21" in b_label
        ref_flag = " [B21/B31 ref]" if is_ref else ""
        print(f"{combo:<55} ${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>6.0f}  "
              f"${econ['net_per_cycle']:>8.0f}   {econ['cycle_days']:>6.1f}d   "
              f"${econ['net_per_month']:>6.0f}   {econ['sustainability']:>5.2f}x"
              f"{flag}{ref_flag}")

    print()
    print("=" * 100)
    print("VERDICT SUMMARY")
    print("=" * 100)
    for _, combo, econ, a_label, b_label in rows:
        if "r0.75 (B21" in b_label:
            continue  # skip reference rows in verdict
        beats_b21 = (econ["net_per_month"] >= B21_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B21_BENCHMARK["sustainability"])
        useful = econ["net_per_month"] >= 300.0 and econ["sustainability"] >= B21_BENCHMARK["sustainability"]
        over_conservative = econ["sustainability"] > B21_BENCHMARK["sustainability"] and econ["net_per_month"] < 300.0
        if beats_b21:
            verdict = "CANDIDATE — beats B21 on both criteria"
        elif useful:
            verdict = f"partial — useful (sust {econ['sustainability']:.2f}x, $/mo ${econ['net_per_month']:.0f})"
        elif over_conservative:
            verdict = f"over-conservative — sust {econ['sustainability']:.2f}x but $/mo only ${econ['net_per_month']:.0f} (too low)"
        else:
            verdict = f"rejected — sust {econ['sustainability']:.2f}x below B21 criterion (2.62x)"
        print(f"  {combo}: ${econ['net_per_month']:.0f}/mo, sust {econ['sustainability']:.2f}x — {verdict}")

    print()
    print("Definitions:")
    print("  Reset$/acct   = combine_attempts_per_funded * $150")
    print("  XFA$/acct     = total_xfa_net / total_accounts")
    print("  Net/cycle     = XFA$/acct - Reset$/acct")
    print("  Cycle days    = combine_days_per_funded + xfa_days_per_account")
    print("  Net/mo        = Net/cycle * 21 / cycle_days")
    print("  Sustainability = A.combine_passes / B.xfa_busts over same 5y window")


if __name__ == "__main__":
    main()
