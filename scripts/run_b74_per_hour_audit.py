"""
B74: Per-hour iFVG PF audit for deployed close-mode config.

Runs equity_export.py with --trade-csv for 5y (excl 2022 holdout), then
computes per-ET-hour PF for iFVG signals (engine_type=ifvg) under the
deployed config at r_multiple=2.5 (B57 candidate).

Phase 1 GO criterion: at least one ET-hour bucket has PF < 0.90 in 3+/5
years AND overall PF < 0.90.

Phase 1 also checks per-hour PF for ORB signals separately to identify
which engine drives any loss-making hour.
"""
from __future__ import annotations

import asyncio
import csv
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone
import zoneinfo

_ET = zoneinfo.ZoneInfo("America/New_York")
_REPO = Path(__file__).resolve().parent.parent

BARS = str(_REPO / "bars" / "bars_MNQ_dbv_2021_2026.csv")
OUT_DIR = _REPO / "research" / "equity_b74"
TRADE_CSV = _REPO / "research" / "mfe_mae_deployed_close.csv"


def run_export(r_multiple: str) -> Path:
    """Run equity_export for all 5y excl 2022 at the given r_multiple."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_csv = OUT_DIR / f"equity_r{r_multiple.replace('.', 'p')}.csv"
    cmd = [
        sys.executable, str(_REPO / "scripts" / "equity_export.py"),
        "--bars", BARS,
        "--instrument", "MNQ",
        "--timeframe", "5min",
        "--risk-pct", "1.0",
        "--partial-r", "0",
        "--exclude-years", "2022",
        "--killzones", "all",
        "--set", f"engine=combined",
        "--set", "ifvg_entry_mode=close",
        "--set", "swing_stop_lookback=30",
        "--set", f"r_multiple={r_multiple}",
        "--set", "stop_buffer=3.0",
        "--set", "min_absolute_body=5.0",
        "--trade-csv", str(TRADE_CSV),
        "--out", str(out_csv),
    ]
    print(f"\n[equity_export] r_multiple={r_multiple} -> {out_csv.name}")
    result = subprocess.run(cmd, cwd=_REPO, capture_output=True, text=True)
    if result.returncode != 0:
        print("STDERR:", result.stderr[-2000:])
        raise RuntimeError(f"equity_export failed: {result.stderr[-500:]}")
    print(result.stdout.strip())
    return out_csv


def load_trades(path: Path) -> list[dict]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            r["pnl"] = Decimal(r["pnl_usd"])
            r["et_hour"] = datetime.fromisoformat(r["entry_ts"]).astimezone(_ET).hour
            r["year"] = datetime.fromisoformat(r["entry_ts"]).year
            rows.append(r)
    return rows


def pf(wins: Decimal, losses: Decimal) -> str:
    if losses == 0:
        return "INF" if wins > 0 else "N/A"
    return f"{wins / losses:.3f}"


def per_hour_analysis(trades: list[dict], engine: str, label: str) -> dict[int, dict]:
    """Compute per-hour stats for a specific engine type."""
    subset = [t for t in trades if t["engine_type"] == engine]
    print(f"\n--- {label} (n={len(subset)}, engine={engine}) ---")
    print(f"{'Hour':>5} {'n':>5} {'WR%':>6} {'PF':>7}  {'2021':>5} {'2023':>5} {'2024':>5} {'2025':>5} {'2026':>5}")
    print("-" * 65)

    hours: dict[int, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"g": Decimal(0), "l": Decimal(0), "n": 0}))
    for t in subset:
        h = t["et_hour"]
        y = str(t["year"])
        p = t["pnl"]
        hours[h][y]["n"] += 1
        if p > 0:
            hours[h][y]["g"] += p
        else:
            hours[h][y]["l"] += abs(p)
        hours[h]["ALL"]["n"] += 1
        if p > 0:
            hours[h]["ALL"]["g"] += p
        else:
            hours[h]["ALL"]["l"] += abs(p)

    results: dict[int, dict] = {}
    for h in sorted(hours.keys()):
        a = hours[h]["ALL"]
        overall_pf = pf(a["g"], a["l"])
        wr = 100 * sum(1 for t in subset if t["et_hour"] == h and t["pnl"] > 0) / a["n"] if a["n"] > 0 else 0.0
        year_pfs = []
        for y in ["2021", "2023", "2024", "2025", "2026"]:
            yd = hours[h].get(y, {"g": Decimal(0), "l": Decimal(0), "n": 0})
            year_pfs.append(pf(yd["g"], yd["l"]))
        ystr = "  ".join(f"{p:>5}" for p in year_pfs)
        print(f"{h:>5}  {a['n']:>5} {wr:>5.1f}%  {overall_pf:>7}  {ystr}")
        results[h] = {
            "n": a["n"],
            "pf_str": overall_pf,
            "pf": a["g"] / a["l"] if a["l"] > 0 else None,
            "year_pfs": dict(zip(["2021", "2023", "2024", "2025", "2026"], year_pfs)),
        }
    return results


def go_nogo_check(results: dict[int, dict]) -> list[int]:
    """Find hours meeting GO criterion: PF < 0.90 in 3+/5 years AND overall PF < 0.90."""
    go_hours = []
    years = ["2021", "2023", "2024", "2025", "2026"]
    for h, d in sorted(results.items()):
        if d["pf"] is None or d["pf"] >= Decimal("0.90"):
            continue
        # Count years with PF < 0.90
        bad_years = sum(
            1 for y in years
            if d["year_pfs"][y] not in ("INF", "N/A") and Decimal(d["year_pfs"][y]) < Decimal("0.90")
        )
        if bad_years >= 3:
            go_hours.append(h)
            print(f"  [GO] Hour {h}ET: overall PF={d['pf_str']}, loss-making in {bad_years}/5 years")
    return go_hours


def main() -> None:
    # Run with r_multiple=2.5 (B57 candidate — primary analysis)
    run_export("2.5")
    trades = load_trades(TRADE_CSV)
    print(f"\nTotal trades loaded: {len(trades)}")
    print(f"  iFVG: {sum(1 for t in trades if t['engine_type']=='ifvg')}")
    print(f"  ORB:  {sum(1 for t in trades if t['engine_type']=='orb')}")

    ifvg_results = per_hour_analysis(trades, "ifvg", "iFVG per-hour PF (r=2.5, deployed close-mode, 5y excl 2022)")
    orb_results  = per_hour_analysis(trades, "orb",  "ORB per-hour PF (r=2.5, 5y excl 2022)")

    print("\n=== PHASE 1 GO/NO-GO ===")
    print("GO criterion: overall PF < 0.90 AND loss-making in 3+/5 years")
    print("\niFVG loss-making hours:")
    ifvg_go = go_nogo_check(ifvg_results)
    print("\nORB loss-making hours:")
    orb_go  = go_nogo_check(orb_results)

    if ifvg_go or orb_go:
        print(f"\nVERDICT: PHASE 1 GO")
        print(f"  iFVG loss-making hours: {ifvg_go}")
        print(f"  ORB loss-making hours:  {orb_go}")
        print("  -> Phase 2: test ifvg_block_hours with the identified hours")
    else:
        print("\nVERDICT: PHASE 1 NO-GO")
        print("  No hour bucket has PF < 0.90 in 3+/5 years in the deployed config")
        print("  -> Lesson: deployed close-mode config has no loss-making hour windows")


if __name__ == "__main__":
    main()
