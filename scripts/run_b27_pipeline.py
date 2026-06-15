"""
B27 driver: two-phase pipeline — iFVG-close Combine (Phase A) → ORB-reentry Funded (Phase B).

Hypothesis: B21 used equity_b1/control_r1p25 (ifvg_edge Phase A, 34 passes over 5y).
B24 confirmed close mode gives +57% more combine passes (11/61 vs 7/61). Regenerating
Phase A equity with close mode should produce ~53 passes — reducing reset cost from $715
to ~$461 and improving sust from 2.62x to ~4.08x. This validates whether the deployed
config (close mode) produces materially better pipeline economics than the B21 numbers imply.

Phase A equity: equity_b27/close_r{risk_str}_{year}.csv (iFVG close mode, r1.25 or r1.0)
  Config: ifvg_entry_mode=close, partial_r=0, swing_stop_lookback=0,
          target_clarity_mode=reject (research baseline with close mode, same as B24)
Phase B equity: equity_b21/orb_reentry_r0p75_{year}.csv (ORB with reentry_after_stop)
  — unchanged from B21

Success criteria (vs B21: $497/mo, sust 2.62x):
  - PRIMARY: net/month AND sust both improve vs B21 on the r0.75 pair
  - If sust improves but $/mo decreases: still a win (sustainability is the harder constraint)

Usage:
    python scripts/run_b27_pipeline.py
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
EQUITY_DIR_B27 = _REPO_ROOT / "research" / "equity_b27"
YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B21_BENCHMARK = {"net_per_month": 497.0, "sustainability": 2.62}
B3_BENCHMARK = {"net_per_month": 393.0, "sustainability": 1.26}

PHASE_A_CONFIGS = {
    "iFVG-close r1.25": (EQUITY_DIR_B27, "close", "r1p25"),
    "iFVG-close r1.0":  (EQUITY_DIR_B27, "close", "r1p0"),
}

PHASE_B_CONFIGS = {
    "ORB-reentry r0.75": (EQUITY_DIR_B21, "orb_reentry", "r0p75"),
    "ORB-reentry r1.0":  (EQUITY_DIR_B21, "orb_reentry", "r1p0"),
    "ORB-reentry r1.25": (EQUITY_DIR_B21, "orb_reentry", "r1p25"),
}

# B21/B3 reference configs for comparison
B21_REFERENCE_A = {
    "iFVG-edge r1.25 (B21)": (EQUITY_DIR_B1, "control", "r1p25"),
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
    print("B27 Pipeline Analysis: iFVG-close Combine -> ORB-reentry Funded")
    print(f"(haircut=${HAIRCUT}, reset_fee=${COMBINE_RESET_FEE}/attempt, years={YEARS})")
    print(f"Success criteria vs B21: $/mo >= {B21_BENCHMARK['net_per_month']} AND "
          f"sust >= {B21_BENCHMARK['sustainability']}\n")

    all_configs: dict[str, tuple] = {
        **PHASE_A_CONFIGS,
        **PHASE_B_CONFIGS,
        **B21_REFERENCE_A,
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
    print("=" * 100)
    print("B27 TWO-PHASE PIPELINE: iFVG-close (Combine) -> ORB-reentry (Funded)")
    print("Sustainability = A.combine_passes / B.xfa_busts (>1 = self-sustaining)")
    print(f"B21 benchmark: iFVG-edge r1.25 -> ORB-reentry r0.75 = $497/mo, sust 2.62x")
    print(f"B3 benchmark:  iFVG-edge r1.25 -> ORB r1.0        = $393/mo, sust 1.26x")
    print("=" * 100)
    print(f"{'Phase A -> Phase B':<44} {'Reset$':<8} {'XFA$':<8} {'Net/cycle':<11} "
          f"{'Cycle days':<12} {'Net/mo':<9} {'Sustain':<9} {'vs B21'}")
    print("-" * 100)

    rows = []
    for a_label in PHASE_A_CONFIGS:
        if a_label not in stats:
            continue
        a_s = stats[a_label]
        for b_label in PHASE_B_CONFIGS:
            if b_label not in stats:
                continue
            b_s = stats[b_label]
            econ = pipeline_economics(a_s, b_s)
            combo = f"{a_label} -> {b_label}"
            rows.append((a_label, b_label, econ))

    # B21 reference (ifvg-edge -> ORB-reentry r0.75)
    ref_a = stats.get("iFVG-edge r1.25 (B21)")
    for b_label in PHASE_B_CONFIGS:
        if ref_a is None or b_label not in stats:
            continue
        b_s = stats[b_label]
        econ = pipeline_economics(ref_a, b_s)
        combo = f"iFVG-edge r1.25 (B21) -> {b_label}"
        rows.append(("iFVG-edge r1.25 (B21)", b_label, econ))

    rows.sort(key=lambda r: -r[2]["net_per_month"])

    for a_label, b_label, econ in rows:
        is_ref = "B21" in a_label
        beats_b21 = (econ["net_per_month"] >= B21_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B21_BENCHMARK["sustainability"])
        flag = " BEATS B21" if (beats_b21 and not is_ref) else (" (B21 ref)" if is_ref else "")
        sust_flag = "***" if econ["sustainability"] >= B21_BENCHMARK["sustainability"] else "   "
        combo = f"{a_label} -> {b_label}"
        print(f"{combo:<44} ${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>6.0f}  "
              f"${econ['net_per_cycle']:>8.0f}   {econ['cycle_days']:>8.1f}d   "
              f"${econ['net_per_month']:>6.0f}   {sust_flag}{econ['sustainability']:>5.2f}x"
              f"{flag}")

    print()
    print("=" * 100)
    print("VERDICT SUMMARY")
    print("=" * 100)
    for a_label, b_label, econ in rows:
        if "B21" in a_label:
            continue
        beats_b21 = (econ["net_per_month"] >= B21_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B21_BENCHMARK["sustainability"])
        beats_b3 = (econ["net_per_month"] >= B3_BENCHMARK["net_per_month"]
                    and econ["sustainability"] >= B3_BENCHMARK["sustainability"])
        if beats_b21:
            verdict = "CANDIDATE — beats B21 on BOTH criteria"
        elif beats_b3:
            verdict = "partial — beats B3 but not B21"
        else:
            verdict = "below B3 threshold"
        combo = f"{a_label} -> {b_label}"
        print(f"  {combo}: ${econ['net_per_month']:.0f}/mo, sust {econ['sustainability']:.2f}x — {verdict}")

    print()
    print("Definitions:")
    print("  Reset$/acct   = combine_attempts_per_funded * $150")
    print("  XFA$/acct     = total_xfa_net / total_accounts")
    print("  Net/cycle     = XFA$/acct - Reset$/acct")
    print("  Cycle days    = combine_days_per_funded + xfa_days_per_account")
    print("  Net/mo        = Net/cycle * 21 / cycle_days")
    print("  Sustainability = A.combine_passes / B.xfa_busts over same 5y window")
    print("  B21 baseline  = iFVG-edge r1.25 Combine + ORB-reentry r0.75 Funded = $497/mo, sust 2.62x")
    print("  B3 baseline   = iFVG-edge r1.25 Combine + ORB r1.0 Funded          = $393/mo, sust 1.26x")


if __name__ == "__main__":
    main()
