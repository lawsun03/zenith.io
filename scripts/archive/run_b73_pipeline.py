"""
B73 driver: ORBxiFVG directional alignment gate in the Phase A combined engine.

The gate (orb_ifvg_alignment_required, shipped as the B56 alignment gate) suppresses
ORB signals on days with no prior same-direction iFVG signal. B56 tested it as an
ORB-ONLY Phase B gate and it failed by volume starvation (Lesson 109). B73 tests the
SAME gate in the Phase A COMBINED engine, where iFVG still fires on all days so the
~9% ORB-volume cut does not starve the account.

Two comparisons isolate the gate's effect (the gate is orthogonal to iFVG r_multiple):
  - gate @ r=3.5 (deployed MNQ override) vs B42 baseline (equity_b42/deployed_r1p0)
  - gate @ r=2.5 (B57 candidate)         vs B57 baseline (equity_b57/r2p5)

Phase B is the B21 ORB-reentry r0.75 engine, unchanged, shared across all configs.
Methodology matches run_b42_pipeline / run_b70_pipeline (per-year equity stitched,
2022 excluded, haircut $200).

Usage:
    python scripts/run_b73_pipeline.py
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

EQUITY_DIR_B42 = _REPO_ROOT / "research" / "equity_b42"
EQUITY_DIR_B57 = _REPO_ROOT / "research" / "equity_b57"
EQUITY_DIR_B21 = _REPO_ROOT / "research" / "equity_b21"
EQUITY_DIR_B73 = _REPO_ROOT / "research" / "equity_b73"
BARS_YEARLY = _REPO_ROOT / "bars" / "yearly"
EQUITY_EXPORT = _REPO_ROOT / "scripts" / "equity_export.py"
PYTHON = sys.executable

YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B42_BENCHMARK = {"net_per_month": 549.0, "sustainability": 3.23}
B57_BENCHMARK = {"net_per_month": 566.0, "sustainability": 3.54}

# Phase A gate-enabled variants to generate: (r_tag, r_multiple_override_or_None)
# r=None -> use the deployed MNQ override (r=3.5); r="2.5" -> override to B57 candidate.
GATE_VARIANTS = [
    ("gate_r3p5", None),
    ("gate_r2p5", "2.5"),
]


def _generate_phase_a_equity(year: str, r_tag: str, r_override: str | None, out_path: Path) -> None:
    """Generate gate-enabled Phase A equity CSV (deployed combined config + alignment gate)."""
    bars = BARS_YEARLY / f"bars_MNQ_dbv_{year}.csv"
    cmd = [
        PYTHON, str(EQUITY_EXPORT),
        "--bars", str(bars),
        "--instrument", "MNQ",
        "--timeframe", "5min",
        "--risk-pct", "1.0",
        "--partial-r", "1.5",
        "--set", "orb_ifvg_alignment_required=True",
        "--out", str(out_path),
    ]
    if r_override is not None:
        cmd[-2:-2] = ["--set", f"r_multiple={r_override}"]
    print(f"    Generating {out_path.name}...", end=" ", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
    if result.returncode != 0:
        print(f"ERROR\n{result.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)
    print("done")


def _ensure_phase_a_csvs() -> None:
    EQUITY_DIR_B73.mkdir(parents=True, exist_ok=True)
    print("=== Generating gate-enabled Phase A equity CSVs (if missing) ===")
    for r_tag, r_override in GATE_VARIANTS:
        print(f"  {r_tag} (r_override={r_override or 'deployed 3.5'}):")
        for year in YEARS:
            out_path = EQUITY_DIR_B73 / f"{r_tag}_{year}.csv"
            if out_path.exists():
                print(f"    Skipping {out_path.name} (exists)")
            else:
                _generate_phase_a_equity(year, r_tag, r_override, out_path)
    print()


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
        if curve:
            offset = curve[-1][1] - BASELINE
        curve.extend((ts, eq + offset) for ts, eq in seg)
    return curve


def phase_stats(equity_dir: Path, prefix: str) -> dict:
    curve = stitch(equity_dir, prefix)
    if not curve:
        return {}
    daily = daily_pnls_from_equity(curve)
    trading_days = len(daily)
    c = simulate_combines(daily, haircut=HAIRCUT)
    x = simulate_xfa_chain(daily, haircut=HAIRCUT)
    avg_days = (Decimal(str(trading_days)) / Decimal(str(c["attempts"]))
                if c["attempts"] else Decimal("0"))
    attempts_per_funded = (Decimal(str(c["attempts"])) / Decimal(str(c["passes"]))
                           if c["passes"] else Decimal("inf"))
    combine_days = attempts_per_funded * avg_days
    reset_fee = attempts_per_funded * COMBINE_RESET_FEE
    avg_funded_days = (Decimal(str(trading_days)) / Decimal(str(x["accounts"]))
                       if x["accounts"] else Decimal("0"))
    net_per_funded = (x["net_payouts"] / Decimal(str(x["accounts"]))
                      if x["accounts"] else Decimal("0"))
    sustainability = (Decimal(str(c["passes"])) / Decimal(str(x["busts"]))
                      if x["busts"] else Decimal("inf"))
    return {
        "trading_days": trading_days,
        "c_attempts": c["attempts"], "c_passes": c["passes"],
        "c_avg_days": float(avg_days),
        "c_days_per_funded": float(combine_days),
        "c_reset_fee": float(reset_fee),
        "x_accounts": x["accounts"], "x_busts": x["busts"],
        "x_net_payouts": float(x["net_payouts"]),
        "x_net_per_account": float(net_per_funded),
        "x_avg_days": float(avg_funded_days),
        "sustainability": float(sustainability),
    }


def pipeline_economics(a: dict, b: dict) -> dict:
    reset_cost = Decimal(str(a["c_reset_fee"]))
    xfa_net = Decimal(str(b["x_net_per_account"]))
    net_per_cycle = xfa_net - reset_cost
    cycle_days = Decimal(str(a["c_days_per_funded"])) + Decimal(str(b["x_avg_days"]))
    net_per_day = net_per_cycle / cycle_days if cycle_days else Decimal("0")
    net_per_month = net_per_day * TRADING_DAYS_PER_MONTH
    sustainability = (Decimal(str(a["c_passes"])) / Decimal(str(b["x_busts"]))
                      if b["x_busts"] else Decimal("inf"))
    return {
        "reset_cost": float(reset_cost),
        "xfa_net": float(xfa_net),
        "cycle_days": float(cycle_days),
        "net_per_month": float(net_per_month),
        "sustainability": float(sustainability),
    }


def _row(label: str, a: dict, eco: dict) -> str:
    return (f"  {label:<20} A {a['c_passes']:>2}/{a['c_attempts']:<3} "
            f"reset=${a['c_reset_fee']:>4.0f} | "
            f"${eco['net_per_month']:>6.0f}/mo sust={eco['sustainability']:>5.2f}x")


def main() -> None:
    _ensure_phase_a_csvs()

    # Phase B: B21 ORB-reentry r0.75, shared across all configs.
    s_b = phase_stats(EQUITY_DIR_B21, "orb_reentry_r0p75")
    print(f"Phase B (ORB-reentry r0.75): {s_b['x_busts']}/{s_b['x_accounts']} busts, "
          f"${s_b['x_net_per_account']:.0f}/acct, {s_b['x_avg_days']:.1f}d/acct\n")

    configs = [
        ("B42 baseline r3.5", EQUITY_DIR_B42, "deployed_r1p0"),
        ("B73 gate r3.5",     EQUITY_DIR_B73, "gate_r3p5"),
        ("B57 baseline r2.5", EQUITY_DIR_B57, "r2p5"),
        ("B73 gate r2.5",     EQUITY_DIR_B73, "gate_r2p5"),
    ]

    print("=" * 78)
    print("PIPELINE ECONOMICS (Phase A varies, Phase B = ORB-reentry r0.75, haircut $200)")
    print("=" * 78)
    rows = {}
    for label, d, prefix in configs:
        s_a = phase_stats(d, prefix)
        if not s_a:
            print(f"  {label}: MISSING Phase A CSVs"); continue
        eco = pipeline_economics(s_a, s_b)
        rows[label] = (s_a, eco)
        print(_row(label, s_a, eco))

    print()
    print("=== GATE EFFECT (gate vs no-gate at same r) ===")
    for base_label, gate_label, bench in [
        ("B42 baseline r3.5", "B73 gate r3.5", B42_BENCHMARK),
        ("B57 baseline r2.5", "B73 gate r2.5", B57_BENCHMARK),
    ]:
        if base_label not in rows or gate_label not in rows:
            continue
        _, base_eco = rows[base_label]
        _, gate_eco = rows[gate_label]
        d_npm = gate_eco["net_per_month"] - base_eco["net_per_month"]
        d_sust = gate_eco["sustainability"] - base_eco["sustainability"]
        verdict = ("BOTH IMPROVE" if d_npm > 0 and d_sust > 0 else
                   "BOTH WORSE" if d_npm < 0 and d_sust < 0 else "MIXED")
        print(f"  {gate_label} vs {base_label}: "
              f"d$/mo={d_npm:+.0f}, dsust={d_sust:+.2f}x -> {verdict}")

    # B73 success criterion: gate r2.5 must beat B57 on BOTH metrics.
    if "B73 gate r2.5" in rows:
        _, g = rows["B73 gate r2.5"]
        beats_b57 = (g["net_per_month"] > B57_BENCHMARK["net_per_month"]
                     and g["sustainability"] > B57_BENCHMARK["sustainability"])
        worse_both_b42 = (g["net_per_month"] <= B42_BENCHMARK["net_per_month"]
                          and g["sustainability"] <= B42_BENCHMARK["sustainability"])
        print()
        if beats_b57:
            print("VERDICT: CANDIDATE — gate r2.5 beats B57 on both $/mo AND sust (2022 holdout required).")
        elif worse_both_b42:
            print("VERDICT: REJECTED — gate r2.5 worse than B42 on BOTH metrics (stop rule).")
        else:
            print("VERDICT: MIXED/NO-GO — gate r2.5 does not beat B57 on both metrics.")
    print()


if __name__ == "__main__":
    main()
