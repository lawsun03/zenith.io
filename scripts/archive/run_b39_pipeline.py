"""
B39 driver: B21 research-baseline Phase A config-parity test at r=2.0.

Hypothesis: B31's candidate ($508/mo, sust=2.85x) used deployed Phase A settings
(engine=combined, all-day killzones, MNQ body=5.0/stop=3.0/r_mult=3.5 overrides).
B21's benchmark ($497/mo, sust=2.62x) used the research baseline (engine=ifvg,
named sessions, ifvg_edge, no MNQ body/stop/r_mult overrides). These are different
configs — we can't cleanly isolate the r=2.0 contribution from the config differences.

Clean test: B21 research-baseline Phase A at r=2.0 (same as B21 but risk=2.0%):
  equity_b39/ifvg_edge_r2p0_{year}.csv
  Flags: engine=ifvg, min_absolute_body=1.0, stop_buffer=0.30, r_multiple=2.5,
         killzones=london,ny_am,ny_pm, ifvg_entry_mode=ifvg_edge,
         swing_stop_lookback=0, target_clarity_mode=reject, partial_r=0

Phase B: equity_b21/orb_reentry_r0p75_{year}.csv (unchanged from B21)

Compare to:
  B21: ifvg_edge r=1.25 research baseline -> ORB-reentry r=0.75: $497/mo, sust=2.62x
  B31: deployed r=2.0 -> ORB-reentry r=0.75: $508/mo, sust=2.85x

This answers: is r=2.0's +$11/mo improvement real and config-agnostic, or an artifact
of the deployed Phase A config being different from B21?

Usage:
    python scripts/run_b39_pipeline.py
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
EQUITY_DIR_B39 = _REPO_ROOT / "research" / "equity_b39"
YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B21_BENCHMARK = {"net_per_month": 497.0, "sustainability": 2.62}
B31_BENCHMARK = {"net_per_month": 508.0, "sustainability": 2.85}

# Phase A configs to compare:
#   B21 reference  — ifvg_edge r=1.25 research baseline (the prior champion)
#   B31 reference  — deployed r=2.0 (engine=combined, MNQ overrides) — prior B31 winner
#   B39 candidate  — ifvg_edge r=2.0 research baseline (isolates risk-level effect)
PHASE_A_CONFIGS = {
    "iFVG-edge r=1.25 (B21 ref)":    (EQUITY_DIR_B1,  "control",        "r1p25"),
    "iFVG deployed r=2.0 (B31 ref)":  (EQUITY_DIR_B31, "ifvg",           "r2p0"),
    "iFVG-edge r=2.0 (B39 clean)":    (EQUITY_DIR_B39, "ifvg_edge_r2p0", ""),
}

PHASE_B_CONFIG = ("ORB-reentry r0.75 (B21 Phase B)", EQUITY_DIR_B21, "orb_reentry", "r0p75")


def stitch(equity_dir: Path, variant: str, risk_str: str) -> list[tuple[datetime, Decimal]]:
    """Stitch per-year equity CSVs into a single continuous curve."""
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in YEARS:
        if risk_str:
            candidates = [
                equity_dir / f"{variant}_{risk_str}_{year}.csv",
                equity_dir / f"{variant}_{year}.csv",
            ]
        else:
            # B39 files are named ifvg_edge_r2p0_{year}.csv (no separate risk_str needed)
            candidates = [
                equity_dir / f"{variant}_{year}.csv",
            ]
        p = next((c for c in candidates if c.exists()), None)
        if p is None:
            print(f"  WARNING: missing equity CSV for {variant!r} {risk_str!r} {year} "
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
    print("B39 Pipeline Analysis: B21 research-baseline Phase A config-parity test at r=2.0")
    print(f"(haircut=${HAIRCUT}, reset_fee=${COMBINE_RESET_FEE}/attempt, years={YEARS})")
    print(f"B21 benchmark: $497/mo, sust 2.62x | B31 winner: $508/mo, sust 2.85x\n")
    print("Question: Is B31's +$11/mo over B21 from the risk level, or from the deployed config?\n")

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
            f"(avg {s['c_avg_days']:.1f}d/attempt, {s['c_days_per_funded']:.1f}d/funded) "
            f"reset ${s['c_reset_fee']:.0f}/funded | "
            f"standalone_sust={s['sustainability']:.2f}x"
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
    print("=" * 110)
    print("B39 TWO-PHASE PIPELINE: Phase A (varied config) -> ORB-reentry r0.75 Phase B")
    print("Sustainability = A.combine_passes / B.xfa_busts (>1 = self-sustaining)")
    print(f"Benchmarks: B21=$497/mo sust=2.62x | B31=$508/mo sust=2.85x")
    print("=" * 110)
    print(f"{'Phase A config':<38} {'Passes':<10} {'d/funded':<10} {'Reset$':<8} {'XFA$':<8} "
          f"{'Cycle d':<9} {'Net/mo':<9} {'Sust':<8} {'vs B21'}")
    print("-" * 110)

    rows = []
    for a_label, a_s in a_stats.items():
        econ = pipeline_economics(a_s, b_s)
        rows.append((econ["net_per_month"], a_label, a_s, econ))
    rows.sort(key=lambda r: -r[0])

    for _, a_label, a_s, econ in rows:
        beats_b31 = (econ["net_per_month"] >= B31_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B31_BENCHMARK["sustainability"])
        beats_b21 = (econ["net_per_month"] >= B21_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B21_BENCHMARK["sustainability"])
        if beats_b31:
            flag = " *** BEATS B31"
        elif beats_b21:
            flag = " ** BEATS B21"
        elif "(B21 ref)" in a_label:
            flag = " [B21 ref]"
        elif "(B31 ref)" in a_label:
            flag = " [B31 ref]"
        else:
            flag = ""
        print(f"{a_label:<38} {a_s['c_passes']}/{a_s['c_attempts']:<7} "
              f"{a_s['c_days_per_funded']:>6.1f}d    "
              f"${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>6.0f}  "
              f"{econ['cycle_days']:>6.1f}d   "
              f"${econ['net_per_month']:>6.0f}   {econ['sustainability']:>5.2f}x"
              f"{flag}")

    print()
    print("=" * 110)
    print("VERDICT & CONFIG-ISOLATION ANALYSIS")
    print("=" * 110)
    b39_label = "iFVG-edge r=2.0 (B39 clean)"
    b21_label = "iFVG-edge r=1.25 (B21 ref)"
    b31_label = "iFVG deployed r=2.0 (B31 ref)"

    b39 = next((econ for _, lbl, _, econ in rows if "(B39" in lbl), None)
    b21 = next((econ for _, lbl, _, econ in rows if "(B21" in lbl), None)
    b31 = next((econ for _, lbl, _, econ in rows if "(B31" in lbl), None)
    b39_a = next((a_s for _, lbl, a_s, _ in rows if "(B39" in lbl), None)
    b31_a = next((a_s for _, lbl, a_s, _ in rows if "(B31" in lbl), None)

    if b39 and b21:
        delta_mo = b39["net_per_month"] - b21["net_per_month"]
        delta_sust = b39["sustainability"] - b21["sustainability"]
        print(f"\nB39 (r=2.0 research) vs B21 (r=1.25 research) — isolates pure risk-level effect:")
        print(f"  Net/mo: ${b39['net_per_month']:.0f} vs ${b21['net_per_month']:.0f} "
              f"(delta={delta_mo:+.0f})")
        print(f"  Sust:   {b39['sustainability']:.2f}x vs {b21['sustainability']:.2f}x "
              f"(delta={delta_sust:+.2f})")
        if delta_mo > 0 and delta_sust >= 0:
            print("  => r=2.0 advantage is REAL and config-agnostic (both metrics improve)")
        elif delta_mo > 0 and delta_sust < 0:
            print("  => r=2.0 improves $/mo but reduces sust — trade-off")
        else:
            print("  => r=2.0 at research baseline does NOT improve B21 — B31 gain is config-specific")

    if b39 and b31 and b39_a and b31_a:
        print(f"\nB39 (r=2.0 research) vs B31 (r=2.0 deployed) — isolates config effect at same risk:")
        print(f"  Passes: {b39_a['c_passes']} (research) vs {b31_a['c_passes']} (deployed)")
        print(f"  Net/mo: ${b39['net_per_month']:.0f} vs ${b31['net_per_month']:.0f}")
        print(f"  Sust:   {b39['sustainability']:.2f}x vs {b31['sustainability']:.2f}x")
        if b31_a["c_passes"] > b39_a["c_passes"]:
            print(f"  => Deployed config generates {b31_a['c_passes']-b39_a['c_passes']} more passes "
                  "at same risk — config differences favor deployed")
        elif b39_a["c_passes"] > b31_a["c_passes"]:
            print(f"  => Research baseline generates {b39_a['c_passes']-b31_a['c_passes']} more passes "
                  "at same risk — research config is better for Phase A")
        else:
            print("  => Same pass count — config differences don't affect Phase A combine at r=2.0")

    print()
    for _nm, a_label, a_s, econ in rows:
        if "(B21 ref)" in a_label or "(B31 ref)" in a_label:
            continue  # only judge B39 candidate
        beats_b31 = (econ["net_per_month"] >= B31_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B31_BENCHMARK["sustainability"])
        beats_b21 = (econ["net_per_month"] >= B21_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B21_BENCHMARK["sustainability"])
        stop_rule = (econ["net_per_month"] < B21_BENCHMARK["net_per_month"]
                     and econ["sustainability"] < B21_BENCHMARK["sustainability"])
        if beats_b31:
            verdict = "CANDIDATE — beats B31 on both criteria"
        elif beats_b21:
            verdict = "partial — beats B21 but not B31"
        elif stop_rule:
            verdict = "STOP RULE — both $/mo and sust below B21"
        else:
            verdict = ("rejected — sust below 2.62x"
                       if econ["sustainability"] < B21_BENCHMARK["sustainability"]
                       else f"partial — $/mo ${econ['net_per_month']:.0f} below B21")
        print(f"B39 verdict: ${econ['net_per_month']:.0f}/mo, sust {econ['sustainability']:.2f}x — {verdict}")

    print()
    print("Definitions:")
    print("  Reset$/acct   = combine_attempts_per_funded * $150")
    print("  XFA$/acct     = total_xfa_net / total_accounts (Phase B fixed = ORB-reentry r0.75)")
    print("  Net/cycle     = XFA$/acct - Reset$/acct")
    print("  Cycle days    = combine_days_per_funded + xfa_days_per_account")
    print("  Net/mo        = Net/cycle * 21 / cycle_days")
    print("  Sustainability = A.combine_passes / B.xfa_busts (per-year methodology)")


if __name__ == "__main__":
    main()
