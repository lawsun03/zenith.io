"""
B88 Phase 1 analysis: FVG zone-width quality gate.

Reads the deployed-config iFVG trade CSV (mfe_mae_deployed_b88.csv, generated
by equity_export --trade-csv with the deployed combined+close+all-day config),
buckets by fvg_zone_pts quintile, and reports PF per bucket and per year.

GO criterion: bottom quintile (narrow FVG) PF >= 1.25x top quintile (wide FVG)
AND directional (narrow > wide) for >= 3/5 years.

Usage:
    python scripts/analyze_b88_fvg_zone_width.py research/mfe_mae_deployed_b88.csv
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def pf(rows: list[dict]) -> float:
    gross_win = sum(float(r["pnl_usd"]) for r in rows if float(r["pnl_usd"]) > 0)
    gross_loss = sum(-float(r["pnl_usd"]) for r in rows if float(r["pnl_usd"]) < 0)
    return gross_win / gross_loss if gross_loss > 0 else float("inf")


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("research/mfe_mae_deployed_b88.csv")
    rows = list(csv.DictReader(path.open()))
    print(f"Total rows: {len(rows)}")

    # Filter to iFVG trades with fvg_zone_pts present
    ifvg = [r for r in rows if r.get("engine_type") == "ifvg" and r.get("fvg_zone_pts")]
    print(f"iFVG rows with fvg_zone_pts: {len(ifvg)}")
    no_zone = [r for r in rows if r.get("engine_type") == "ifvg" and not r.get("fvg_zone_pts")]
    print(f"iFVG rows WITHOUT fvg_zone_pts (displacement-only?): {len(no_zone)}")

    if not ifvg:
        print("No iFVG rows with fvg_zone_pts — aborting.")
        return

    zone_widths = [float(r["fvg_zone_pts"]) for r in ifvg]
    zone_widths_sorted = sorted(zone_widths)
    n = len(zone_widths_sorted)
    q_bounds = [
        zone_widths_sorted[n // 5],
        zone_widths_sorted[2 * n // 5],
        zone_widths_sorted[3 * n // 5],
        zone_widths_sorted[4 * n // 5],
    ]
    print(f"\nZone width distribution (n={n}):")
    print(f"  min={zone_widths_sorted[0]:.2f}  Q1<={q_bounds[0]:.2f}  Q2<={q_bounds[1]:.2f}  "
          f"Q3<={q_bounds[2]:.2f}  Q4<={q_bounds[3]:.2f}  max={zone_widths_sorted[-1]:.2f}")

    def bucket(row: dict) -> int:
        w = float(row["fvg_zone_pts"])
        if w <= q_bounds[0]:
            return 1
        elif w <= q_bounds[1]:
            return 2
        elif w <= q_bounds[2]:
            return 3
        elif w <= q_bounds[3]:
            return 4
        else:
            return 5

    by_bucket: dict[int, list[dict]] = defaultdict(list)
    for r in ifvg:
        by_bucket[bucket(r)].append(r)

    print(f"\nBucket summary (Q1=narrowest, Q5=widest):")
    print(f"{'Q':>4}  {'n':>5}  {'PF':>6}  {'Net$':>10}  {'Zone range'}")
    bucket_pfs = {}
    for q in range(1, 6):
        brows = by_bucket[q]
        p = pf(brows)
        bucket_pfs[q] = p
        net = sum(float(r["pnl_usd"]) for r in brows)
        w_vals = [float(r["fvg_zone_pts"]) for r in brows]
        print(f"  Q{q}  {len(brows):>5}  {p:>6.3f}  {net:>10.0f}  "
              f"{min(w_vals):.2f}–{max(w_vals):.2f}")

    narrow_pf = bucket_pfs[1]
    wide_pf = bucket_pfs[5]
    ratio = narrow_pf / wide_pf if wide_pf > 0 else float("inf")
    print(f"\nNarrow (Q1) PF: {narrow_pf:.3f}")
    print(f"Wide   (Q5) PF: {wide_pf:.3f}")
    print(f"Ratio narrow/wide: {ratio:.3f}  (GO threshold: >= 1.25)")

    # Per-year breakdown
    by_year_bucket: dict[str, dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in ifvg:
        yr = r["entry_ts"][:4]
        by_year_bucket[yr][bucket(r)].append(r)

    print(f"\nPer-year Q1 vs Q5 PF (GO = narrow > wide):")
    go_years = 0
    years_shown = sorted(by_year_bucket.keys())
    for yr in years_shown:
        q1r = by_year_bucket[yr][1]
        q5r = by_year_bucket[yr][5]
        pf1 = pf(q1r) if q1r else float("nan")
        pf5 = pf(q5r) if q5r else float("nan")
        ratio_yr = pf1 / pf5 if pf5 > 0 and q5r else float("nan")
        go = "GO " if pf1 > pf5 and q1r and q5r else "NO "
        if q1r and q5r and pf1 > pf5:
            go_years += 1
        print(f"  {yr}: Q1 n={len(q1r)} PF={pf1:.3f}  Q5 n={len(q5r)} PF={pf5:.3f}  "
              f"ratio={ratio_yr:.3f}  {go}")

    print(f"\nYears with Q1 > Q5: {go_years}/{len(years_shown)} "
          f"(GO threshold: 3/{len(years_shown)})")

    # Final verdict
    monotone_check = (
        bucket_pfs[1] >= bucket_pfs[2] and
        bucket_pfs[2] >= bucket_pfs[3] and
        bucket_pfs[3] >= bucket_pfs[4] and
        bucket_pfs[4] >= bucket_pfs[5]
    )
    print(f"\nMonotone (Q1>=Q2>=Q3>=Q4>=Q5): {monotone_check}")
    print(f"\n{'GO' if ratio >= 1.25 and go_years >= 3 else 'NO-GO'}: "
          f"ratio={ratio:.3f} vs 1.25 threshold, "
          f"{go_years}/{len(years_shown)} years consistent")


if __name__ == "__main__":
    main()
