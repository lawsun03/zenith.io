"""
B20 driver: two-phase pipeline — iFVG Combine (Phase A) → LongOnly-iFVG Funded (Phase B).

Hypothesis: B3's best pair is iFVG r1.25 Combine + ORB r1.0 Funded = $393/mo, sust 1.26x.
Long-only iFVG funded (B19: PF 1.173, sust 1.60x at r1.25 using london+ny_am killzones)
may beat this when coupled with the fast iFVG Combine (28.6d per funded account).

Phase A equity: equity_b1/control_r1p25_{year}.csv (iFVG r1.25, same as B3)
Phase B equity: equity_b20/longonly_r{risk}_{year}.csv (LongOnly iFVG, london+ny_am, B19 config)

Success criteria (vs B3: $393/mo, sust 1.26x):
  - PRIMARY: $/mo >= $393 AND sust >= 1.26x on at least one risk level

Usage:
    python scripts/run_b20_pipeline.py
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
EQUITY_DIR_B20 = _REPO_ROOT / "research" / "equity_b20"
YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B3_BENCHMARK = {"net_per_month": 393.0, "sustainability": 1.26}

PHASE_A_CONFIGS = {
    "iFVG r1.25": (EQUITY_DIR_B1, "control", "1p25"),
}

PHASE_B_CONFIGS = {
    "LongOnly r0.75": (EQUITY_DIR_B20, "longonly", "r0p75"),
    "LongOnly r1.0":  (EQUITY_DIR_B20, "longonly", "r1p0"),
    "LongOnly r1.25": (EQUITY_DIR_B20, "longonly", "r1p25"),
}

# B3 reference configs for single-phase and baseline two-phase
B3_ORB_CONFIGS = {
    "ORB r0.75 (B3)": (EQUITY_DIR_B1, "orb", "0p75"),
    "ORB r1.0 (B3)":  (EQUITY_DIR_B1, "orb", "1"),
}


def stitch(equity_dir: Path, variant: str, risk_str: str) -> list[tuple[datetime, Decimal]]:
    """Stitch per-year equity CSVs into a single continuous curve."""
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in YEARS:
        # Support both "variant_r{risk_str}_{year}" and "variant_{risk_str}_{year}" naming
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
    print(f"B20 Pipeline Analysis: iFVG Combine -> LongOnly-iFVG Funded")
    print(f"(haircut=${HAIRCUT}, reset_fee=${COMBINE_RESET_FEE}/attempt, years={YEARS})")
    print(f"Success criteria vs B3: $/mo >= {B3_BENCHMARK['net_per_month']} AND "
          f"sust >= {B3_BENCHMARK['sustainability']}\n")

    all_configs: dict[str, tuple] = {
        **PHASE_A_CONFIGS,
        **PHASE_B_CONFIGS,
        **B3_ORB_CONFIGS,
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
    print("=" * 90)
    print("B20 TWO-PHASE PIPELINE: iFVG (Combine) -> LongOnly-iFVG (Funded)")
    print("Sustainability = A.combine_passes / B.xfa_busts (>1 = self-sustaining)")
    print(f"B3 benchmark: iFVG r1.25 -> ORB r1.0 = $393/mo, sust 1.26x")
    print("=" * 90)
    print(f"{'Phase A -> Phase B':<35} {'Reset$':<8} {'XFA$':<8} {'Net/cycle':<11} "
          f"{'Cycle days':<12} {'Net/mo':<9} {'Sustain':<9} {'vs B3'}")
    print("-" * 90)

    rows = []
    a_label = "iFVG r1.25"
    if a_label not in stats:
        print(f"ERROR: Phase A config '{a_label}' not loaded — aborting.")
        return
    a_s = stats[a_label]

    for b_label in PHASE_B_CONFIGS:
        if b_label not in stats:
            continue
        b_s = stats[b_label]
        econ = pipeline_economics(a_s, b_s)
        combo = f"{a_label} -> {b_label}"
        rows.append((econ["net_per_month"], combo, econ))

    # Also include B3 reference pairs for comparison
    for b_label in B3_ORB_CONFIGS:
        if b_label not in stats:
            continue
        b_s = stats[b_label]
        econ = pipeline_economics(a_s, b_s)
        combo = f"{a_label} -> {b_label}"
        rows.append((econ["net_per_month"], combo, econ))

    rows.sort(key=lambda r: -r[0])
    for _, combo, econ in rows:
        beats_b3 = (econ["net_per_month"] >= B3_BENCHMARK["net_per_month"]
                    and econ["sustainability"] >= B3_BENCHMARK["sustainability"])
        flag = " BEATS B3" if beats_b3 else (" (B3 ref)" if "B3" in combo else "")
        sust_flag = "***" if econ["sustainability"] >= B3_BENCHMARK["sustainability"] else "   "
        print(f"{combo:<35} ${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>6.0f}  "
              f"${econ['net_per_cycle']:>8.0f}   {econ['cycle_days']:>8.1f}d   "
              f"${econ['net_per_month']:>6.0f}   {sust_flag}{econ['sustainability']:>5.2f}x"
              f"{flag}")

    print()
    print("=" * 90)
    print("VERDICT SUMMARY")
    print("=" * 90)
    b3_row = next((r for r in rows if "ORB r1.0" in r[1]), None)
    for _, combo, econ in rows:
        if "B3" in combo:
            continue
        beats = (econ["net_per_month"] >= B3_BENCHMARK["net_per_month"]
                 and econ["sustainability"] >= B3_BENCHMARK["sustainability"])
        verdict = "CANDIDATE — beats B3 on both criteria" if beats else "below B3 threshold"
        print(f"  {combo}: ${econ['net_per_month']:.0f}/mo, sust {econ['sustainability']:.2f}x — {verdict}")

    print()
    print("Definitions:")
    print("  Reset$/acct   = combine_attempts_per_funded * $150")
    print("  XFA$/acct     = total_xfa_net / total_accounts")
    print("  Net/cycle     = XFA$/acct - Reset$/acct")
    print("  Cycle days    = combine_days_per_funded + xfa_days_per_account")
    print("  Net/mo        = Net/cycle * 21 / cycle_days")
    print("  Sustainability = A.combine_passes / B.xfa_busts over same 5y window")
    print("  B3 baseline   = iFVG r1.25 Combine + ORB r1.0 Funded = $393/mo, sust 1.26x")


if __name__ == "__main__":
    main()
