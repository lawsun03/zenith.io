"""B57 confirmatory 2022 holdout analysis for r=2.5 vs r=3.5 baseline."""
from __future__ import annotations
import csv, sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from app.backtest.funded_sim import daily_pnls_from_equity, simulate_combines, simulate_xfa_chain

HAIRCUT = Decimal("200")
BASELINE = Decimal("50000")
YEARS_5 = ["2021", "2023", "2024", "2025", "2026"]  # excl 2022
YEARS_6 = ["2021", "2022", "2023", "2024", "2025", "2026"]  # incl 2022


def stitch(eq_dir: Path, prefix: str, years: list[str]) -> list[tuple[datetime, Decimal]]:
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in years:
        p = eq_dir / f"{prefix}_{year}.csv"
        if not p.exists():
            print(f"  missing {p}", file=sys.stderr)
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


def summary(label: str, eq_dir: Path, prefix: str, years: list[str]) -> None:
    curve = stitch(eq_dir, prefix, years)
    if not curve:
        print(f"  {label}: NO DATA")
        return
    daily = daily_pnls_from_equity(curve)
    c = simulate_combines(daily, haircut=HAIRCUT)
    x = simulate_xfa_chain(daily, haircut=HAIRCUT)
    print(f"  {label}: combine {c['passes']}/{c['attempts']} ({c['passes']/c['attempts']*100:.1f}%)  "
          f"XFA busts {x['busts']}/{x['accounts']}  net ${x['net_payouts']:.0f}")


eq_b57 = _ROOT / "research" / "equity_b57"
eq_b42 = _ROOT / "research" / "equity_b42"

print("=== 5y (excl 2022) — primary analysis ===")
summary("r=2.5 (5y)", eq_b57, "r2p5", YEARS_5)
summary("r=3.5 B42  (5y)", eq_b42, "deployed_r1p0", YEARS_5)

print("\n=== 6y (incl 2022) — confirmatory holdout ===")
summary("r=2.5 (incl 2022)", eq_b57, "r2p5", YEARS_6)
summary("r=3.5 B42 (incl 2022)", eq_b42, "deployed_r1p0", YEARS_6)
