"""B44 funded-pipeline benchmark: iFVG block_hours=[11,12,13] vs baseline.

Runs 4 configs × 5 years (excl 2022) sequentially, stitches per-year curves,
runs funded_sim at haircut=$200, prints comparison table.

Configs:
  1. all-sides + ifvg_edge + all-day, no block (research baseline)
  2. all-sides + ifvg_edge + all-day + block=[11,12,13]
  3. LongOnly + close + all-day, no block (B24-comparable)
  4. LongOnly + close + all-day + block=[11,12,13]
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

YEARS = ["2021", "2023", "2024", "2025", "2026"]
BARS_YEARLY = _REPO_ROOT / "bars" / "yearly"
EQUITY_DIR = _REPO_ROOT / "research" / "equity_b44"
EQUITY_EXPORT = _REPO_ROOT / "scripts" / "equity_export.py"
PYTHON = sys.executable
HAIRCUT = Decimal("200")
BASELINE = Decimal("50000")

CONFIGS = [
    {
        "name": "allsides_baseline",
        "label": "All-sides ifvg_edge all-day (baseline)",
        "extra_flags": [
            "--risk-pct", "1.25", "--partial-r", "0",
            "--set", "swing_stop_lookback=0",
        ],
    },
    {
        "name": "allsides_block",
        "label": "All-sides ifvg_edge all-day + block=[11,12,13]",
        "extra_flags": [
            "--risk-pct", "1.25", "--partial-r", "0",
            "--set", "swing_stop_lookback=0",
            "--set", "ifvg_block_hours=11,12,13",
        ],
    },
    {
        "name": "lo_close_baseline",
        "label": "LongOnly close all-day (B24-comparable baseline)",
        "extra_flags": [
            "--risk-pct", "1.25", "--partial-r", "0",
            "--set", "swing_stop_lookback=0",
            "--set", "allowed_sides=long",
            "--set", "ifvg_entry_mode=close",
            "--killzones", "all",
        ],
    },
    {
        "name": "lo_close_block",
        "label": "LongOnly close all-day + block=[11,12,13]",
        "extra_flags": [
            "--risk-pct", "1.25", "--partial-r", "0",
            "--set", "swing_stop_lookback=0",
            "--set", "allowed_sides=long",
            "--set", "ifvg_entry_mode=close",
            "--killzones", "all",
            "--set", "ifvg_block_hours=11,12,13",
        ],
    },
]


def run_equity_export(year: str, cfg_name: str, extra_flags: list[str]) -> Path:
    out = EQUITY_DIR / f"b44_{cfg_name}_{year}.csv"
    if out.exists():
        print(f"  skip {out.name} (exists)", flush=True)
        return out
    bars = BARS_YEARLY / f"bars_MNQ_dbv_{year}.csv"
    cmd = [
        PYTHON, str(EQUITY_EXPORT),
        "--bars", str(bars),
        "--instrument", "MNQ",
        "--timeframe", "5min",
        "--out", str(out),
    ] + extra_flags
    print(f"  running {out.name}...", end=" ", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
    if r.returncode != 0:
        print(f"ERROR\n{r.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)
    last_line = r.stdout.strip().split("\n")[-1]
    print(last_line, flush=True)
    return out


def stitch(cfg_name: str) -> list[tuple[datetime, Decimal]]:
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in YEARS:
        p = EQUITY_DIR / f"b44_{cfg_name}_{year}.csv"
        if not p.exists():
            print(f"  WARNING: missing {p.name}", file=sys.stderr)
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


def stats(curve: list[tuple[datetime, Decimal]]) -> dict:
    if not curve:
        return {}
    daily = daily_pnls_from_equity(curve)
    trading_days = len(daily)
    c = simulate_combines(daily, haircut=HAIRCUT)
    x = simulate_xfa_chain(daily, haircut=HAIRCUT)
    sust = (
        Decimal(str(x["accounts"])) / Decimal(str(x["busts"]))
        if x["busts"] else Decimal("inf")
    )
    return {
        "combine_passes": c["passes"],
        "combine_busts": c["busts"],
        "combine_attempts": c["attempts"],
        "xfa_accounts": x["accounts"],
        "xfa_busts": x["busts"],
        "xfa_net": x["net_payouts"],
        "sust": float(sust),
        "trading_days": trading_days,
    }


def main() -> None:
    EQUITY_DIR.mkdir(parents=True, exist_ok=True)
    results = {}
    for cfg in CONFIGS:
        print(f"\n=== {cfg['label']} ===")
        for year in YEARS:
            run_equity_export(year, cfg["name"], cfg["extra_flags"])
        curve = stitch(cfg["name"])
        s = stats(curve)
        results[cfg["name"]] = {"label": cfg["label"], **s}
        print(f"  combine: {s.get('combine_passes',0)}/{s.get('combine_attempts',0)} passes "
              f"| xfa: {s.get('xfa_accounts',0)} accts {s.get('xfa_busts',0)} busts "
              f"| net ${s.get('xfa_net',0):.0f} | sust {s.get('sust',0):.2f}x")

    print("\n" + "=" * 80)
    print("B44 SUMMARY — funded pipeline comparison (5y excl 2022, haircut $200)")
    print("=" * 80)
    print(f"{'Config':<40} {'Passes':>7} {'XFA Accts':>9} {'XFA Busts':>9} {'Net $':>10} {'Sust':>7}")
    print("-" * 80)
    for name, r in results.items():
        print(f"{r['label']:<40} {r.get('combine_passes',0):>7} "
              f"{r.get('xfa_accounts',0):>9} {r.get('xfa_busts',0):>9} "
              f"{r.get('xfa_net',0):>10.0f} {r.get('sust',0):>6.2f}x")
    print("-" * 80)
    print("B24 reference (LongOnly close london+ny_am, from B24 benchmark): "
          "sust=2.524x, funded PF=1.167")


if __name__ == "__main__":
    main()
