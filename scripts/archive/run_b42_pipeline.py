"""
B42 driver: Deployed config full end-to-end pipeline simulation.

Tests what the ACTUAL deployed bot config earns in the funded pipeline.
Deployed config (bot_config.json as of 2026-06-14):
  engine=combined, ifvg_entry_mode=close, partial_r=1.5, swing_stop_lookback=30,
  killzones=all, risk=1.0%, min_absolute_body=5.0 (MNQ), stop_buffer=3.0 (MNQ),
  r_multiple=3.5 (MNQ iFVG), orb_r_multiple=2.5 (MNQ ORB).

Phase A equity: equity_b42/deployed_r1p0_{year}.csv (and r2p0)
Phase B equity: equity_b21/orb_reentry_r0p75_{year}.csv (unchanged from B21)

Standalone deployed: funded_sim on deployed equity (both-phases-same-config picture)
Two-phase: deployed Phase A + ORB-reentry r=0.75 Phase B

Success criteria (vs B31 winner: $508/mo, sust 2.85x):
  - PRIMARY: $/mo >= $508 AND sust >= 2.85x (beats B31 on BOTH metrics)
  - SECONDARY: $/mo >= $497 AND sust >= 2.62x (beats B21)
  - If either metric < B21: below B21 threshold

Usage:
    python scripts/run_b42_pipeline.py
"""
from __future__ import annotations

import csv
import subprocess
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

EQUITY_DIR_B1  = _REPO_ROOT / "research" / "equity_b1"
EQUITY_DIR_B21 = _REPO_ROOT / "research" / "equity_b21"
EQUITY_DIR_B31 = _REPO_ROOT / "research" / "equity_b31"
EQUITY_DIR_B42 = _REPO_ROOT / "research" / "equity_b42"
YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B21_BENCHMARK = {"net_per_month": 497.0, "sustainability": 2.62}
B31_BENCHMARK = {"net_per_month": 508.0, "sustainability": 2.85}

BARS_YEARLY = _REPO_ROOT / "bars" / "yearly"
EQUITY_EXPORT = _REPO_ROOT / "scripts" / "equity_export.py"
PYTHON = sys.executable


def _generate_equity_csv(year: str, risk_pct: str, out_path: Path) -> None:
    """Generate equity CSV using deployed config (from bot_config.json defaults)."""
    bars = BARS_YEARLY / f"bars_MNQ_dbv_{year}.csv"
    cmd = [
        PYTHON, str(EQUITY_EXPORT),
        "--bars", str(bars),
        "--instrument", "MNQ",
        "--timeframe", "5min",
        "--risk-pct", risk_pct,
        "--partial-r", "1.5",
        "--out", str(out_path),
    ]
    print(f"  Generating {out_path.name}...", end=" ", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
    if r.returncode != 0:
        print(f"ERROR\n{r.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)
    print("done")


def _ensure_equity_csvs() -> None:
    """Generate any missing deployed equity CSVs."""
    print("=== Generating deployed equity CSVs (if missing) ===")
    for year in YEARS:
        for risk_pct, tag in [("1.0", "r1p0"), ("2.0", "r2p0")]:
            out_path = EQUITY_DIR_B42 / f"deployed_{tag}_{year}.csv"
            if out_path.exists():
                print(f"  Skipping {out_path.name} (exists)")
            else:
                _generate_equity_csv(year, risk_pct, out_path)
    print()


def stitch(equity_dir: Path, prefix: str, year_list: list[str] = YEARS) -> list[tuple[datetime, Decimal]]:
    """Stitch per-year equity CSVs into a single continuous curve."""
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in year_list:
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


def phase_stats(equity_dir: Path, prefix: str, label: str) -> dict:
    """Return combine and xfa stats for this config at HAIRCUT."""
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
        x["net_payouts"] / Decimal(str(x["accounts"]))
        if x["accounts"] else Decimal("0")
    )
    sustainability = (
        Decimal(str(c["passes"])) / Decimal(str(x["busts"]))
        if x["busts"] else Decimal("inf")
    )
    return {
        "label": label, "prefix": prefix,
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


def standalone_monthly_net(s: dict) -> float:
    """Net monthly earnings from standalone funded_sim (same strategy both phases)."""
    # standalone: funded accounts earn x_net_per_account, cost x_reset_fee per cycle
    net_per_cycle = s["x_net_per_account"] - s["c_reset_fee"]
    cycle_days = s["c_days_per_funded"] + s["x_avg_days"]
    if cycle_days == 0:
        return 0.0
    return float(Decimal(str(net_per_cycle)) / Decimal(str(cycle_days)) * TRADING_DAYS_PER_MONTH)


def main() -> None:
    _ensure_equity_csvs()

    print("=== Phase A: Loading deployed config equity stats ===")
    phase_a_configs = [
        (EQUITY_DIR_B42, "deployed_r1p0", "B42 deployed r=1.0%"),
        (EQUITY_DIR_B42, "deployed_r2p0", "B42 deployed r=2.0%"),
        # Reference Phase A configs
        (EQUITY_DIR_B1,  "control_r1p25", "B21 iFVG-edge r=1.25% (ref)"),
        (EQUITY_DIR_B31, "ifvg_r2p0",     "B31 deployed-edge r=2.0% (ref)"),
    ]

    stats: dict[str, dict] = {}
    for equity_dir, prefix, label in phase_a_configs:
        print(f"  Loading {label}...", end=" ", flush=True)
        s = phase_stats(equity_dir, prefix, label)
        if not s:
            print("MISSING — skipped")
            continue
        stats[label] = s
        print(
            f"combine {s['c_passes']}/{s['c_attempts']} "
            f"(avg {s['c_avg_days']:.1f}d/attempt, {s['c_days_per_funded']:.1f}d/funded, "
            f"${s['c_reset_fee']:.0f}/funded)"
        )

    print("\n=== Phase B: Loading ORB-reentry r=0.75 equity stats ===")
    b_label = "ORB-reentry r0.75 (B21 Phase B)"
    s_b = phase_stats(EQUITY_DIR_B21, "orb_reentry_r0p75", b_label)
    if s_b:
        stats[b_label] = s_b
        print(
            f"  {b_label}: xfa busts={s_b['x_busts']}/{s_b['x_accounts']} "
            f"net ${s_b['x_net_payouts']:.0f} "
            f"(${s_b['x_net_per_account']:.0f}/acct, {s_b['x_avg_days']:.1f}d/acct) "
            f"sust={s_b['sustainability']:.2f}x"
        )
    else:
        print("  MISSING — cannot compute two-phase results")
        return

    print()
    print("=" * 110)
    print("B42 STANDALONE RESULTS (same deployed strategy for Phase A and Phase B)")
    print("(strategy runs Combine then Funded phases without config switch)")
    print("=" * 110)
    for equity_dir, prefix, label in phase_a_configs[:2]:  # only B42 variants
        if label not in stats:
            continue
        s = stats[label]
        net_mo = standalone_monthly_net(s)
        print(f"  {label}: ${net_mo:.0f}/mo standalone (sust {s['sustainability']:.2f}x, "
              f"{s['x_accounts']} accts, {s['x_busts']} busts)")

    print()
    print("=" * 110)
    print("B42 TWO-PHASE PIPELINE MATRIX (deployed Phase A -> ORB-reentry r0.75 Phase B)")
    print("Sustainability = Phase_A.combine_passes / Phase_B.xfa_busts (>1 = self-sustaining)")
    print("=" * 110)
    hdr = (f"{'Phase A -> Phase B':<55} {'Reset$':>7} {'XFA$':>7} "
           f"{'Net/cyc':>9} {'Cycle d':>9} {'Net/mo':>8} {'Sust':>8}")
    print(hdr)
    print("-" * 110)

    rows = []
    for equity_dir, prefix, a_label in phase_a_configs:
        if a_label not in stats:
            continue
        a_s = stats[a_label]
        econ = pipeline_economics(a_s, s_b)
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
        elif "(ref)" in a_label:
            flag = " [ref]"
        print(f"{combo:<55} ${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>5.0f}  "
              f"${econ['net_per_cycle']:>7.0f}  {econ['cycle_days']:>7.1f}d  "
              f"${econ['net_per_month']:>6.0f}  {econ['sustainability']:>6.2f}x{flag}")

    print()
    print("=" * 110)
    print("VERDICT")
    print("=" * 110)
    for _, combo, econ, a_label in rows:
        if "(ref)" in a_label:
            continue
        beats_b31 = (econ["net_per_month"] >= B31_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B31_BENCHMARK["sustainability"])
        beats_b21 = (econ["net_per_month"] >= B21_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B21_BENCHMARK["sustainability"])
        if beats_b31:
            verdict = f"CANDIDATE — beats B31 ($508/mo, 2.85x) on BOTH metrics"
        elif beats_b21:
            verdict = f"CANDIDATE — beats B21 ($497/mo, 2.62x) but not B31"
        else:
            verdict = f"below B21 threshold ($497/mo, 2.62x)"
        print(f"  {combo}: ${econ['net_per_month']:.0f}/mo, sust {econ['sustainability']:.2f}x — {verdict}")

    print()
    print("Note: B42 deployed config uses r_multiple=3.5 (MNQ iFVG override) vs 2.5 in B21/B31.")
    print("These numbers are NOT directly comparable to B21/B31 on the Phase A iFVG side.")
    print("B42 reveals the ACTUAL deployed pipeline economics, not a comparable research benchmark.")
    print()
    print("Definitions:")
    print("  Standalone $/mo   = (XFA$/acct - Reset$/acct) * 21 / (combine_d + funded_d)")
    print("  Two-phase $/mo    = same formula but Phase B uses ORB-reentry equity")
    print("  Sustainability    = A.combine_passes / B.xfa_busts over same 5y window")
    print("  B21 baseline      = iFVG r1.25 Combine + ORB-reentry r0.75 Funded = $497/mo, sust 2.62x")
    print("  B31 winner        = iFVG-edge r2.0 deployed + ORB-reentry r0.75 = $508/mo, sust 2.85x")


if __name__ == "__main__":
    main()
