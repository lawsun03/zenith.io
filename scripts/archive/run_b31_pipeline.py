"""
B31 driver: Phase A higher-risk sensitivity (r=2.0) to increase annual combine passes.

Hypothesis: B21 Phase A runs iFVG r=1.25%. At r=2.0%, each winning trade earns 1.6x more
-- fewer winning trades needed to reach the $3k threshold. Expected monthly gain rises from
~$3.5k (r=1.25) to ~$5.6k (r=2.0), making the $3k target easier to reach on average.
This should increase Phase A pass rate AND pass speed.

Phase A equity: equity_b31/ifvg_r{risk_str}_{year}.csv (r=1.5 and r=2.0)
Phase B equity: equity_b21/orb_reentry_r0p75_{year}.csv (unchanged from B21)

Success criteria (vs B21: $497/mo, sust 2.62x):
  - PRIMARY: $/mo improves AND sust stays >= 2.62x (both criteria)
  - SECONDARY: any risk level that improves $/mo while sust >= 1.26x (B3 baseline)

Usage:
    python scripts/run_b31_pipeline.py
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
EQUITY_DIR_B31 = _REPO_ROOT / "research" / "equity_b31"
YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B21_BENCHMARK = {"net_per_month": 497.0, "sustainability": 2.62}
B3_BENCHMARK = {"net_per_month": 393.0, "sustainability": 1.26}

# NOTE: B31 Phase A equity uses deployed bot_config.json settings at the time of generation:
#   engine=combined, min_absolute_body=5.0 (MNQ override), stop_buffer=3.0 (MNQ override),
#   enabled_killzones=all (all-day), PLUS research overrides:
#   ifvg_entry_mode=ifvg_edge, target_clarity_mode=reject, swing_stop_lookback=0, partial_r=0
#
# This config differs from B21 research baseline (engine=ifvg, body=1.0, stop=0.30, named sessions).
# The B21 reference row shows how many passes the original research baseline produced;
# B31 r=1.25 is the within-B31 baseline at deployed settings.
# Comparison to B21's $497/mo is indicative but not directly equivalent due to different trade counts.

PHASE_A_CONFIGS = {
    "iFVG r1.25 (B31-base)": (EQUITY_DIR_B31, "ifvg", "r1p25"),  # within-B31 baseline
    "iFVG r1.5 (B31)":       (EQUITY_DIR_B31, "ifvg", "r1p5"),
    "iFVG r2.0 (B31)":       (EQUITY_DIR_B31, "ifvg", "r2p0"),
    "iFVG r1.25 (B21-ref)":  (EQUITY_DIR_B1, "control", "r1p25"),  # research baseline ref
}

PHASE_B_CONFIG = ("ORB-reentry r0.75 (B21)", EQUITY_DIR_B21, "orb_reentry", "r0p75")


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


def phase_stats(label: str, equity_dir: Path, variant: str, risk_str: str) -> dict:
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
        "label": label, "variant": variant, "risk_str": risk_str,
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
    print("B31 Pipeline Analysis: Phase A higher-risk sensitivity (r=1.5 and r=2.0)")
    print(f"(haircut=${HAIRCUT}, reset_fee=${COMBINE_RESET_FEE}/attempt, years={YEARS})")
    print(f"Success criteria vs B21: $/mo >= {B21_BENCHMARK['net_per_month']} "
          f"AND sust >= {B21_BENCHMARK['sustainability']}\n")

    # Load Phase A configs
    a_stats: dict[str, dict] = {}
    print("=== PHASE A STANDALONE STATS (iFVG combine performance) ===")
    for label, (eq_dir, variant, risk_str) in PHASE_A_CONFIGS.items():
        print(f"Loading {label}...", end=" ", flush=True)
        s = phase_stats(label, eq_dir, variant, risk_str)
        if not s:
            print("MISSING — skipped")
            continue
        a_stats[label] = s
        print(
            f"combine {s['c_passes']}/{s['c_attempts']} "
            f"(avg {s['c_avg_days']:.1f}d/attempt, {s['c_days_per_funded']:.1f}d/funded) | "
            f"standalone sust={s['sustainability']:.2f}x"
        )

    # Load Phase B
    b_label, b_eq_dir, b_variant, b_risk_str = PHASE_B_CONFIG
    print(f"\nLoading Phase B {b_label}...", end=" ", flush=True)
    b_s = phase_stats(b_label, b_eq_dir, b_variant, b_risk_str)
    if not b_s:
        print("MISSING — aborting")
        return
    print(
        f"xfa busts={b_s['x_busts']}/{b_s['x_accounts']} "
        f"net ${b_s['x_net_payouts']:.0f} "
        f"(${b_s['x_net_per_account']:.0f}/acct, {b_s['x_avg_days']:.1f}d/acct)"
    )

    print()
    print("=" * 100)
    print("B31 TWO-PHASE PIPELINE: iFVG Phase A (varied risk) -> ORB-reentry r0.75 Phase B")
    print("Sustainability = A.combine_passes / B.xfa_busts (>1 = self-sustaining)")
    print(f"B21 benchmark: iFVG r1.25 -> ORB-reentry r0.75 = $497/mo, sust 2.62x")
    print("=" * 100)
    print(f"{'Phase A':<28} {'Passes':<9} {'d/funded':<10} {'Reset$':<8} {'XFA$':<8} "
          f"{'Net/cycle':<11} {'Cycle days':<12} {'Net/mo':<9} {'Sustain':<10} {'vs B21'}")
    print("-" * 100)

    rows = []
    for a_label, a_s in a_stats.items():
        econ = pipeline_economics(a_s, b_s)
        rows.append((econ["net_per_month"], a_label, a_s, econ))

    rows.sort(key=lambda r: -r[0])
    for _, a_label, a_s, econ in rows:
        beats_b21 = (econ["net_per_month"] >= B21_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B21_BENCHMARK["sustainability"])
        beats_b3 = (econ["net_per_month"] >= B3_BENCHMARK["net_per_month"]
                    and econ["sustainability"] >= B3_BENCHMARK["sustainability"])
        if beats_b21:
            flag = " BEATS B21"
        elif beats_b3:
            flag = " beats B3 only"
        elif "B21" in a_label:
            flag = " (B21 ref)"
        else:
            flag = " below B3"
        sust_flag = "***" if econ["sustainability"] >= B21_BENCHMARK["sustainability"] else "   "
        print(f"{a_label:<28} {a_s['c_passes']}/{a_s['c_attempts']:<6} "
              f"{a_s['c_days_per_funded']:>6.1f}d    "
              f"${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>6.0f}  "
              f"${econ['net_per_cycle']:>8.0f}   {econ['cycle_days']:>8.1f}d   "
              f"${econ['net_per_month']:>6.0f}   {sust_flag}{econ['sustainability']:>5.2f}x"
              f"{flag}")

    print()
    print("=" * 100)
    print("VERDICT SUMMARY")
    print("=" * 100)
    for _, a_label, a_s, econ in rows:
        beats_b21 = (econ["net_per_month"] >= B21_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B21_BENCHMARK["sustainability"])
        beats_b3 = (econ["net_per_month"] >= B3_BENCHMARK["net_per_month"]
                    and econ["sustainability"] >= B3_BENCHMARK["sustainability"])
        if beats_b21:
            verdict = "CANDIDATE — beats B21 on both criteria"
        elif beats_b3:
            verdict = "partial — beats B3 but not B21"
        else:
            verdict = "below B3 threshold — rejected"
        print(f"  {a_label}: {a_s['c_passes']}/{a_s['c_attempts']} passes, "
              f"${econ['net_per_month']:.0f}/mo, sust {econ['sustainability']:.2f}x — {verdict}")

    print()
    print("Definitions:")
    print("  Reset$/acct   = combine_attempts_per_funded * $150")
    print("  XFA$/acct     = total_xfa_net / total_accounts (Phase B fixed = ORB-reentry r0.75)")
    print("  Net/cycle     = XFA$/acct - Reset$/acct")
    print("  Cycle days    = combine_days_per_funded + xfa_days_per_account")
    print("  Net/mo        = Net/cycle * 21 / cycle_days")
    print("  Sustainability = A.combine_passes / B.xfa_busts over same 5y window")


if __name__ == "__main__":
    main()
