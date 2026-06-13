"""
B30 driver: DOW-filtered ORB-reentry Phase B — two-phase pipeline analysis.

Phase A: iFVG r1.25 (equity_b1/control_r1p25, unchanged from B21)
Phase B: ORB-reentry r0.75 + skip_trading_days=Monday,Wednesday (equity_b30/)

Success criteria (vs B21: $497/mo, sust 2.62x):
  - PRIMARY: busts < 13 AND $/month >= $497 (beats B21 on both criteria)
  - SECONDARY: any improvement in sust (even if $/mo is lower)

Usage:
    python scripts/run_b30_pipeline.py
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

EQUITY_DIR_B1 = _REPO_ROOT / "research" / "equity_b1"
EQUITY_DIR_B21 = _REPO_ROOT / "research" / "equity_b21"
EQUITY_DIR_B30 = _REPO_ROOT / "research" / "equity_b30"
YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B21_BENCHMARK = {"net_per_month": 497.0, "sustainability": 2.62, "busts": 13}
B3_BENCHMARK = {"net_per_month": 393.0, "sustainability": 1.26}

PHASE_A_CONFIGS = {
    "iFVG r1.25": (EQUITY_DIR_B1, "control", "r1p25"),
}

PHASE_B_CONFIGS = {
    "ORB-reentry skip Mon+Wed r0.75": (EQUITY_DIR_B30, "orb_reentry_skip_monwed", "r0p75"),
}

# Reference configs for comparison
REF_CONFIGS = {
    "ORB-reentry r0.75 (B21)": (EQUITY_DIR_B21, "orb_reentry", "r0p75"),
}


def stitch(equity_dir: Path, variant: str, risk_str: str) -> list[tuple[datetime, Decimal]]:
    """Stitch per-year equity CSVs into a single continuous curve."""
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in YEARS:
        candidates = [
            equity_dir / f"{variant}_{risk_str}_{year}.csv",
            equity_dir / f"{variant}_r{risk_str}_{year}.csv",
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
    print("B30 Pipeline Analysis: iFVG Combine -> ORB-reentry (skip Mon+Wed) Funded")
    print(f"(haircut=${HAIRCUT}, reset_fee=${COMBINE_RESET_FEE}/attempt, years={YEARS})")
    print(f"Success criteria vs B21: $/mo >= {B21_BENCHMARK['net_per_month']} AND "
          f"sust >= {B21_BENCHMARK['sustainability']}\n")

    all_configs: dict[str, tuple] = {
        **PHASE_A_CONFIGS,
        **PHASE_B_CONFIGS,
        **REF_CONFIGS,
    }

    stats: dict[str, dict] = {}
    for label, args in all_configs.items():
        equity_dir, variant, risk_str = args
        print(f"Loading {label} ({variant} {risk_str})...", end=" ", flush=True)
        s = phase_stats(equity_dir, variant, risk_str)
        if not s:
            print("MISSING — skipped")
            continue
        stats[label] = s
        print(
            f"combine {s['c_passes']}/{s['c_attempts']} "
            f"(avg {s['c_avg_days']:.1f}d/attempt, {s['c_days_per_funded']:.1f}d/funded) | "
            f"xfa busts={s['x_busts']}/{s['x_accounts']} "
            f"net ${s['x_net_payouts']:.0f} "
            f"sust={s['sustainability']:.2f}x"
        )

    print()
    print("=" * 95)
    print("B30 TWO-PHASE PIPELINE: iFVG (Combine) -> ORB-reentry skip Mon+Wed (Funded)")
    print("Sustainability = A.combine_passes / B.xfa_busts (>1 = self-sustaining)")
    print(f"B21 benchmark: iFVG r1.25 -> ORB-reentry r0.75 = $497/mo, sust 2.62x, 13 busts")
    print("=" * 95)
    print(f"{'Phase A -> Phase B':<45} {'Reset$':<8} {'XFA$':<8} {'Net/cycle':<11} "
          f"{'Cycle days':<12} {'Net/mo':<9} {'Sustain'}")
    print("-" * 95)

    rows = []
    a_label = "iFVG r1.25"
    if a_label not in stats:
        print(f"ERROR: Phase A config '{a_label}' not loaded — aborting.")
        return
    a_s = stats[a_label]

    for b_label, b_configs in [*PHASE_B_CONFIGS.items(), *REF_CONFIGS.items()]:
        if b_label not in stats:
            continue
        b_s = stats[b_label]
        econ = pipeline_economics(a_s, b_s)
        combo = f"{a_label} -> {b_label}"
        rows.append((econ["net_per_month"], combo, econ, b_s))

    rows.sort(key=lambda r: -r[0])
    for _, combo, econ, b_s in rows:
        beats_b21 = (econ["net_per_month"] >= B21_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B21_BENCHMARK["sustainability"])
        flag = " BEATS B21" if beats_b21 else (" (B21 ref)" if "B21" in combo else "")
        print(f"{combo:<45} ${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>6.0f}  "
              f"${econ['net_per_cycle']:>8.0f}   {econ['cycle_days']:>8.1f}d   "
              f"${econ['net_per_month']:>6.0f}   {econ['sustainability']:>5.2f}x"
              f"{flag}")

    print()
    print("=" * 95)
    print("VERDICT SUMMARY")
    print("=" * 95)
    for _, combo, econ, b_s in rows:
        if "B21" in combo or "B3" in combo:
            continue
        beats = (econ["net_per_month"] >= B21_BENCHMARK["net_per_month"]
                 and econ["sustainability"] >= B21_BENCHMARK["sustainability"])
        verdict = "CANDIDATE — beats B21 on both criteria" if beats else "below B21 threshold"
        print(f"  {combo}: ${econ['net_per_month']:.0f}/mo, sust {econ['sustainability']:.2f}x, "
              f"{b_s['x_busts']} Phase B busts — {verdict}")

    print()
    print("Definitions: same as B21 pipeline model (see run_b21_pipeline.py)")


if __name__ == "__main__":
    main()
