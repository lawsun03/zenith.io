"""
B67 driver: ORB-reentry Phase B orb_r_multiple sensitivity sweep.

Hypothesis: B21/B42 Phase B uses orb_r_multiple=2.5. A lower target (1.5 or 2.0R)
increases WR by bringing target closer to entry -- potentially allowing funded accounts
to reach payout more often before MLL. Higher target (3.0, 3.5) increases per-win payout
but lowers WR. B57 found lower r (2.5 vs 3.5) improved Phase A iFVG pipeline; this tests
the analogous effect on Phase B ORB-reentry.

Phase A equity: equity_b42/deployed_r1p0_{year}.csv (deployed config, existing)
Phase B equity: equity_b67/orb_reentry_r{tag}_{year}.csv (generated here, combined engine,
    partial_r=1.5, varying orb_r_multiple={1.5, 2.0, 2.5, 3.0, 3.5})

Success criteria vs B42 ($549/mo, sust=3.23x):
    PRIMARY: both $/mo AND sust improve on at least one r value.
    Stop rule: all tested r values lose on both metrics vs B42 -> reject.

Usage:
    python scripts/run_b67_pipeline.py
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
EQUITY_DIR_B67 = _REPO_ROOT / "research" / "equity_b67"
BARS_YEARLY = _REPO_ROOT / "bars" / "yearly"
EQUITY_EXPORT = _REPO_ROOT / "scripts" / "equity_export.py"
PYTHON = sys.executable

YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B42_BENCHMARK = {"net_per_month": 549.0, "sustainability": 3.23}

# r_multiple values to test: 2.5 is baseline (already in B21/B42)
ORB_R_VALUES = [
    ("1p5", "1.5"),
    ("2p0", "2.0"),
    ("2p5", "2.5"),   # baseline
    ("3p0", "3.0"),
    ("3p5", "3.5"),
]


def _r_tag_to_float(tag: str) -> float:
    return float(tag.replace("p", "."))


def _generate_phase_b_equity(year: str, r_tag: str, r_val: str, out_path: Path) -> None:
    """Generate Phase B equity CSV: deployed combined engine with varying orb_r_multiple."""
    bars = BARS_YEARLY / f"bars_MNQ_dbv_{year}.csv"
    cmd = [
        PYTHON, str(EQUITY_EXPORT),
        "--bars", str(bars),
        "--instrument", "MNQ",
        "--timeframe", "5min",
        "--risk-pct", "0.75",
        "--partial-r", "1.5",
        "--killzones", "all",
        "--set", "engine=combined",
        "--set", "ifvg_entry_mode=close",
        "--set", "orb_reentry_after_stop=True",
        "--set", f"orb_r_multiple={r_val}",
        "--set", "swing_stop_lookback=0",
        "--set", "stop_buffer=3.0",
        "--set", "min_absolute_body=5.0",
        "--out", str(out_path),
    ]
    print(f"    Generating {out_path.name}...", end=" ", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
    if result.returncode != 0:
        print(f"ERROR\n{result.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)
    print("done")


def _ensure_phase_b_csvs() -> None:
    """Generate any missing Phase B equity CSVs."""
    print("=== Generating Phase B equity CSVs (if missing) ===")
    for r_tag, r_val in ORB_R_VALUES:
        print(f"  orb_r_multiple={r_val}:")
        for year in YEARS:
            out_path = EQUITY_DIR_B67 / f"orb_reentry_r{r_tag}_{year}.csv"
            if out_path.exists():
                print(f"    Skipping {out_path.name} (exists)")
            else:
                _generate_phase_b_equity(year, r_tag, r_val, out_path)
    print()


def stitch(equity_dir: Path, prefix: str) -> list[tuple[datetime, Decimal]]:
    """Stitch per-year equity CSVs into a single continuous curve."""
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
    """Return combine and xfa stats for this config at HAIRCUT."""
    curve = stitch(equity_dir, prefix)
    if not curve:
        return {}
    daily = daily_pnls_from_equity(curve)
    trading_days = len(daily)
    c = simulate_combines(daily, haircut=HAIRCUT)
    x = simulate_xfa_chain(daily, haircut=HAIRCUT)
    avg_days = (
        Decimal(str(trading_days)) / Decimal(str(c["attempts"]))
        if c["attempts"] else Decimal("0")
    )
    attempts_per_funded = (
        Decimal(str(c["attempts"])) / Decimal(str(c["passes"]))
        if c["passes"] else Decimal("inf")
    )
    combine_days = attempts_per_funded * avg_days
    reset_fee = attempts_per_funded * COMBINE_RESET_FEE
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
    _ensure_phase_b_csvs()

    print("=== Phase A: B42 deployed config (existing CSVs) ===")
    s_a = phase_stats(EQUITY_DIR_B42, "deployed_r1p0")
    if not s_a:
        print("ERROR: Missing B42 Phase A equity CSVs", file=sys.stderr)
        sys.exit(1)
    print(
        f"  combine {s_a['c_passes']}/{s_a['c_attempts']} "
        f"(avg {s_a['c_avg_days']:.1f}d/attempt, {s_a['c_days_per_funded']:.1f}d/funded, "
        f"${s_a['c_reset_fee']:.0f}/funded)"
    )

    print("\n=== Phase B: ORB-reentry combined engine varying orb_r_multiple ===")
    print(f"  Config: engine=combined, risk=0.75%, partial_r=1.5, lookback=0, stop=3.0, body=5.0")
    print()

    results: list[tuple[str, dict, dict]] = []
    for r_tag, r_val in ORB_R_VALUES:
        prefix = f"orb_reentry_r{r_tag}"
        s_b = phase_stats(EQUITY_DIR_B67, prefix)
        if not s_b:
            print(f"  r={r_val}: MISSING — skipped")
            continue
        eco = pipeline_economics(s_a, s_b)
        results.append((r_val, s_b, eco))
        marker = " *CANDIDATE*" if (
            eco["net_per_month"] > B42_BENCHMARK["net_per_month"] and
            eco["sustainability"] > B42_BENCHMARK["sustainability"]
        ) else ""
        print(
            f"  r={r_val}: XFA busts={s_b['x_busts']}/{s_b['x_accounts']} "
            f"(${s_b['x_net_per_account']:.0f}/acct, {s_b['x_avg_days']:.1f}d) "
            f"| pipeline ${eco['net_per_month']:.0f}/mo sust={eco['sustainability']:.2f}x{marker}"
        )

    print()
    print("=" * 90)
    print("SUMMARY TABLE (vs B42 baseline: $549/mo, sust=3.23x)")
    print(f"{'r_mult':>6} {'busts':>6} {'accts':>6} {'$/acct':>8} {'$/mo':>8} {'sust':>7} {'verdict':>12}")
    print("-" * 90)
    # Reference line
    print(f"{'B42':>6} {'13':>6} {'14':>6} {'$3131':>8} {'$549':>8} {'3.23x':>7} {'baseline':>12}")
    for r_val, s_b, eco in results:
        npm_vs = "+" if eco["net_per_month"] > B42_BENCHMARK["net_per_month"] else "-"
        sust_vs = "+" if eco["sustainability"] > B42_BENCHMARK["sustainability"] else "-"
        verdict = "CANDIDATE" if (npm_vs == "+" and sust_vs == "+") else f"{npm_vs}npm/{sust_vs}sust"
        print(
            f"  {r_val:>4} {s_b['x_busts']:>6} {s_b['x_accounts']:>6} "
            f"${s_b['x_net_per_account']:>7.0f} ${eco['net_per_month']:>7.0f} "
            f"{eco['sustainability']:>6.2f}x {verdict:>12}"
        )
    print()

    candidates = [(r, s, e) for r, s, e in results
                  if e["net_per_month"] > B42_BENCHMARK["net_per_month"]
                  and e["sustainability"] > B42_BENCHMARK["sustainability"]]
    if candidates:
        best_r, best_s, best_e = max(candidates, key=lambda x: x[2]["net_per_month"])
        print(f"VERDICT: CANDIDATE — best r={best_r}: ${best_e['net_per_month']:.0f}/mo "
              f"sust={best_e['sustainability']:.2f}x")
    else:
        worse_both = all(
            e["net_per_month"] <= B42_BENCHMARK["net_per_month"] and
            e["sustainability"] <= B42_BENCHMARK["sustainability"]
            for _, _, e in results
        )
        if worse_both:
            print("VERDICT: REJECTED — all tested r values worse on BOTH metrics vs B42")
        else:
            print("VERDICT: MIXED — some metrics improve, some regress; no clear win")
    print()


if __name__ == "__main__":
    main()
