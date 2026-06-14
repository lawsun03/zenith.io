"""
B79 Phase 1: iFVG setup freshness analysis in the deployed close-mode config.

Regenerates the deployed-config per-trade dataset (superceding B77 mfe_mae_deployed_combined_clean.csv
with displacement_ts column added), then computes gap_bars = (entry_ts - displacement_ts) / 5min
per iFVG trade and reports PF per freshness bucket.

GO criterion: fresh (1-3 bars) / stale (10+ bars) PF ratio >= 1.30 overall
AND consistent in 3+/5 open years.
"""
from __future__ import annotations

import csv
import io
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BARS_DIR = ROOT / "bars" / "yearly"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
EQUITY_EXPORT = ROOT / "scripts" / "equity_export.py"
OUT_DIR = ROOT / "research" / "equity_b79"
TRADE_CSV_PATH = ROOT / "research" / "mfe_mae_deployed_b79.csv"
YEARS = [2021, 2023, 2024, 2025, 2026]  # 2022 = holdout

# Deployed config params for B79 (same as B77, plus displacement_ts via new column)
DEPLOYED_PARAMS = [
    "--set", "engine=combined",
    "--set", "ifvg_entry_mode=close",
    "--killzones", "all",
    "--set", "r_multiple=2.5",
    "--set", "swing_stop_lookback=30",
    "--set", "stop_buffer=3.0",
    "--set", "min_absolute_body=5.0",
    "--set", "allowed_sides=both",
    "--partial-r", "0",
    "--risk-pct", "1.0",
    "--instrument", "MNQ",
]


def run_year(year: int) -> list[dict]:
    bars_csv = BARS_DIR / f"bars_MNQ_dbv_{year}.csv"
    if not bars_csv.exists():
        print(f"  SKIP {year}: bars file not found", flush=True)
        return []
    out_equity = OUT_DIR / f"deployed_r1p0_{year}.csv"
    out_equity.parent.mkdir(parents=True, exist_ok=True)
    trade_csv = OUT_DIR / f"trades_{year}.csv"

    cmd = [
        str(PYTHON), str(EQUITY_EXPORT),
        "--bars", str(bars_csv),
        "--out", str(out_equity),
        "--trade-csv", str(trade_csv),
    ] + DEPLOYED_PARAMS

    print(f"  running equity_export for {year}...", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"  ERROR {year}: {result.stderr[-500:]}", flush=True)
        return []
    print(f"  {year}: {result.stdout.strip().splitlines()[-1]}", flush=True)

    if not trade_csv.exists():
        return []
    rows = list(csv.DictReader(trade_csv.open(encoding="utf-8")))
    for r in rows:
        r["_year"] = year
    return rows


def compute_gap_bars(entry_ts_str: str, displacement_ts_str: str) -> float | None:
    if not displacement_ts_str:
        return None
    try:
        entry = datetime.fromisoformat(entry_ts_str)
        disp = datetime.fromisoformat(displacement_ts_str)
        gap_secs = (entry - disp).total_seconds()
        return gap_secs / 300.0  # 5-min bars
    except Exception:
        return None


def bucket(gap: float) -> str:
    if gap <= 3:
        return "fresh"
    if gap <= 9:
        return "mid"
    return "stale"


def pf(wins: Decimal, losses: Decimal) -> float:
    if losses == 0:
        return float("inf")
    return float(wins / losses)


def main() -> None:
    print("=== B79 Phase 1: iFVG Freshness in Deployed Config ===\n", flush=True)

    # Collect all per-year trades
    all_trades: list[dict] = []
    for year in YEARS:
        rows = run_year(year)
        all_trades.extend(rows)

    print(f"\nTotal trades: {len(all_trades)}", flush=True)

    # Write combined CSV (supercedes B77 dataset; adds displacement_ts)
    TRADE_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRADE_CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        if all_trades:
            fieldnames = list(all_trades[0].keys())
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(all_trades)
    print(f"Wrote combined dataset -> {TRADE_CSV_PATH}\n", flush=True)

    # Filter to iFVG trades with displacement_ts
    ifvg_trades = [t for t in all_trades if t.get("engine_type") == "ifvg" and t.get("displacement_ts")]
    print(f"iFVG trades with displacement_ts: {len(ifvg_trades)} / {sum(1 for t in all_trades if t.get('engine_type')=='ifvg')} iFVG total\n", flush=True)

    # Overall analysis
    print("=== Overall freshness buckets (5y excl 2022) ===", flush=True)
    bucket_wins: dict[str, Decimal] = defaultdict(Decimal)
    bucket_losses: dict[str, Decimal] = defaultdict(Decimal)
    bucket_n: dict[str, int] = defaultdict(int)

    for t in ifvg_trades:
        gap = compute_gap_bars(t["entry_ts"], t["displacement_ts"])
        if gap is None or gap < 0:
            continue
        b = bucket(gap)
        pnl = Decimal(t["pnl_usd"])
        bucket_n[b] += 1
        if pnl > 0:
            bucket_wins[b] += pnl
        else:
            bucket_losses[b] += abs(pnl)

    for b in ["fresh", "mid", "stale"]:
        n = bucket_n[b]
        pf_val = pf(bucket_wins[b], bucket_losses[b])
        print(f"  {b:8s}: n={n:4d}  PF={pf_val:.3f}  wins=${float(bucket_wins[b]):,.0f}  losses=${float(bucket_losses[b]):,.0f}", flush=True)

    fresh_pf = pf(bucket_wins["fresh"], bucket_losses["fresh"])
    stale_pf = pf(bucket_wins["stale"], bucket_losses["stale"])
    ratio = fresh_pf / stale_pf if stale_pf > 0 else float("inf")
    print(f"\nFresh/Stale PF ratio: {ratio:.3f}  (GO criterion: >= 1.30)", flush=True)

    # Per-year breakdown
    print("\n=== Per-year freshness breakdown ===", flush=True)
    year_ratio_above_threshold = 0
    for year in YEARS:
        year_trades = [t for t in ifvg_trades if t.get("_year") == year]
        yw: dict[str, Decimal] = defaultdict(Decimal)
        yl: dict[str, Decimal] = defaultdict(Decimal)
        yn: dict[str, int] = defaultdict(int)
        for t in year_trades:
            gap = compute_gap_bars(t["entry_ts"], t["displacement_ts"])
            if gap is None or gap < 0:
                continue
            b = bucket(gap)
            pnl = Decimal(t["pnl_usd"])
            yn[b] += 1
            if pnl > 0:
                yw[b] += pnl
            else:
                yl[b] += abs(pnl)
        yr_fresh_pf = pf(yw["fresh"], yl["fresh"])
        yr_stale_pf = pf(yw["stale"], yl["stale"])
        yr_ratio = yr_fresh_pf / yr_stale_pf if yr_stale_pf > 0 else float("inf")
        above = yr_ratio >= 1.30
        if above:
            year_ratio_above_threshold += 1
        print(
            f"  {year}: fresh n={yn['fresh']:3d} PF={yr_fresh_pf:.3f}  "
            f"stale n={yn['stale']:3d} PF={yr_stale_pf:.3f}  "
            f"ratio={yr_ratio:.3f}  {'GO' if above else 'NO-GO'}",
            flush=True,
        )

    print(f"\nYears where fresh/stale ratio >= 1.30: {year_ratio_above_threshold}/5", flush=True)

    # GO / NO-GO verdict
    print("\n=== GO / NO-GO VERDICT ===", flush=True)
    overall_go = ratio >= 1.30
    year_go = year_ratio_above_threshold >= 3
    if overall_go and year_go:
        print(f"GO: ratio={ratio:.3f} >= 1.30 AND {year_ratio_above_threshold}/5 years consistent", flush=True)
        print("Proceed to Phase 2: add ifvg_max_freshness_bars gate.", flush=True)
    else:
        reasons = []
        if not overall_go:
            reasons.append(f"overall ratio {ratio:.3f} < 1.30")
        if not year_go:
            reasons.append(f"only {year_ratio_above_threshold}/5 years consistent (need 3)")
        print(f"NO-GO: {'; '.join(reasons)}", flush=True)
        print("B79 REJECTED (Phase 1 stop rule).", flush=True)

    print(f"\nCombined dataset: {TRADE_CSV_PATH}", flush=True)


if __name__ == "__main__":
    main()
