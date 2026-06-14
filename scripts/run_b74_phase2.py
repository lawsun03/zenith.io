"""
B74 Phase 2: block loss-making iFVG hours in Phase A combined engine.

Phase 1 found (deployed close-mode, r=2.5, 5y excl 2022, n=2376 iFVG):
  - Hour 9ET: PF=0.487, loss-making 5/5 years, n=211 (8.9% of iFVG signals)
  - Hour 6ET: PF=0.578, loss-making 3/5 years, n=84 (3.5% of iFVG signals)

Variants tested:
  A) block_9:   ifvg_block_hours=[9]   - most conservative, 5/5 year consistency
  B) block_6_9: ifvg_block_hours=[6,9] - both confirmed loss-making hours (3+ years)

Success criteria vs B57 ($566/mo, sust=3.54x): BOTH $/mo AND sust must improve.
Stop rule: variant worse on BOTH metrics vs B42 ($549/mo, sust=3.23x) -> reject.
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

EQUITY_EXPORT = _REPO_ROOT / "scripts" / "equity_export.py"
PYTHON = sys.executable

EQUITY_DIR_B57 = _REPO_ROOT / "research" / "equity_b57"   # Phase A r=2.5 no-block
EQUITY_DIR_B21 = _REPO_ROOT / "research" / "equity_b21"   # Phase B ORB-reentry r=0.75
EQUITY_DIR_B74 = _REPO_ROOT / "research" / "equity_b74"
BARS_YEARLY = _REPO_ROOT / "bars" / "yearly"

YEARS = ["2021", "2023", "2024", "2025", "2026"]
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B42_BENCHMARK = {"net_per_month": 549.0, "sustainability": 3.23}
B57_BENCHMARK = {"net_per_month": 566.0, "sustainability": 3.54}


def _gen_phase_a(year: str, prefix: str, block_hours: str) -> Path:
    out_path = EQUITY_DIR_B74 / f"{prefix}_{year}.csv"
    if out_path.exists():
        print(f"    [cache] {out_path.name}")
        return out_path
    bars = BARS_YEARLY / f"bars_MNQ_dbv_{year}.csv"
    cmd = [
        PYTHON, str(EQUITY_EXPORT),
        "--bars", str(bars),
        "--instrument", "MNQ",
        "--timeframe", "5min",
        "--risk-pct", "1.0",
        "--partial-r", "1.5",
        "--killzones", "all",
        "--set", "engine=combined",
        "--set", "ifvg_entry_mode=close",
        "--set", "swing_stop_lookback=30",
        "--set", "r_multiple=2.5",
        "--set", "stop_buffer=3.0",
        "--set", "min_absolute_body=5.0",
        "--set", f"ifvg_block_hours={block_hours}",
        "--out", str(out_path),
    ]
    result = subprocess.run(cmd, cwd=_REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        print("STDERR:", result.stderr[-1000:])
        raise RuntimeError(f"equity_export failed for {year}: {result.stderr[-300:]}")
    print(f"    {result.stdout.strip()}")
    return out_path


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
    combine_days = Decimal(str(a["c_days_per_funded"]))
    funded_days = Decimal(str(b["x_avg_days"]))
    cycle_days = combine_days + funded_days
    net_per_day = net_per_cycle / cycle_days if cycle_days else Decimal("0")
    net_per_month = net_per_day * TRADING_DAYS_PER_MONTH
    sustainability = (Decimal(str(a["c_passes"])) / Decimal(str(b["x_busts"]))
                      if b["x_busts"] else Decimal("inf"))
    return {
        "reset_cost": float(reset_cost),
        "xfa_net": float(xfa_net),
        "net_per_cycle": float(net_per_cycle),
        "cycle_days": float(cycle_days),
        "net_per_month": float(net_per_month),
        "sustainability": float(sustainability),
    }


def main() -> None:
    EQUITY_DIR_B74.mkdir(parents=True, exist_ok=True)

    # Phase B (fixed): B21 ORB-reentry r=0.75
    print("=== Phase B: B21 ORB-reentry r=0.75 (fixed) ===")
    s_b21 = phase_stats(EQUITY_DIR_B21, "orb_reentry_r0p75")
    print(f"  XFA {s_b21['x_busts']}/{s_b21['x_accounts']} accounts, "
          f"${s_b21['x_net_per_account']:.0f}/acct, {s_b21['x_avg_days']:.1f}d/acct")

    # Phase A variants
    variants = [
        ("r2p5_baseline",    "B57 r=2.5 baseline (no block)",   EQUITY_DIR_B57, None),
        ("block9_r2p5",      "block_9 [9ET], r=2.5",            EQUITY_DIR_B74, "9"),
        ("block69_r2p5",     "block_6_9 [6ET,9ET], r=2.5",      EQUITY_DIR_B74, "6,9"),
    ]

    results: list[tuple[str, dict, dict]] = []
    for prefix, label, eq_dir, block_hours in variants:
        print(f"\n=== Phase A: {label} ===")
        if block_hours is not None:
            for year in YEARS:
                _gen_phase_a(year, prefix, block_hours)
        s_a = phase_stats(eq_dir, prefix)
        if not s_a:
            print(f"  MISSING CSVs — skipped")
            continue
        eco = pipeline_economics(s_a, s_b21)
        print(f"  combine: {s_a['c_passes']}/{s_a['c_attempts']} "
              f"(avg {s_a['c_avg_days']:.1f}d/attempt, "
              f"${s_a['c_reset_fee']:.0f}/funded)")
        print(f"  pipeline: ${eco['net_per_month']:.0f}/mo  sust={eco['sustainability']:.2f}x")
        results.append((label, s_a, eco))

    print("\n" + "=" * 80)
    print("SUMMARY (vs B42: $549/mo 3.23x | vs B57: $566/mo 3.54x)")
    print(f"{'Label':32s}  {'A-pass':>6}  {'$/mo':>7}  {'sust':>6}  {'verdict'}")
    print("-" * 80)
    print(f"{'B42 deployed baseline':32s}  {'42':>6}  {'$549':>7}  {'3.23x':>6}  baseline")
    for label, s_a, eco in results:
        npm  = eco["net_per_month"]
        sust = eco["sustainability"]
        vs_b57 = npm > B57_BENCHMARK["net_per_month"] and sust > B57_BENCHMARK["sustainability"]
        vs_b42_either = npm > B42_BENCHMARK["net_per_month"] or sust > B42_BENCHMARK["sustainability"]
        both_worse_b42 = npm < B42_BENCHMARK["net_per_month"] and sust < B42_BENCHMARK["sustainability"]
        if vs_b57:
            verdict = "CANDIDATE"
        elif both_worse_b42:
            verdict = "REJECT (stop rule)"
        else:
            verdict = "MIXED"
        print(f"  {label:30s}  {s_a['c_passes']:>6}  ${npm:>6.0f}  {sust:>5.2f}x  {verdict}")


if __name__ == "__main__":
    main()
