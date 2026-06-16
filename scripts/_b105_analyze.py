"""B105 analysis: run pipeline_variance_summary on ORB-reentry r0.75 per-year equity
(2021/23/24/25/26 excl 2022 holdout, h200, matching B103/B104 baseline).
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from app.backtest.funded_sim import (
    daily_pnls_from_equity,
    pipeline_variance_summary,
    simulate_combines,
    simulate_xfa_chain,
)

YEARS = ["2021", "2023", "2024", "2025", "2026"]
EQUITY_DIR = _REPO / "research" / "equity_b21"
HAIRCUT = Decimal("200")


def load_equity(path: Path) -> list[tuple[datetime, Decimal]]:
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append((datetime.fromisoformat(row["ts"]), Decimal(row["equity"])))
    return rows


def main() -> None:
    # Concatenate daily P&L deltas from all non-2022 years
    all_daily: list[tuple[datetime, Decimal]] = []
    for yr in YEARS:
        path = EQUITY_DIR / f"orb_reentry_r0p75_{yr}.csv"
        if not path.exists():
            print(f"MISSING: {path}", file=sys.stderr)
            sys.exit(1)
        curve = load_equity(path)
        all_daily.extend(daily_pnls_from_equity(curve))
    all_daily.sort(key=lambda x: x[0])

    print(f"Series: {all_daily[0][0].date()} -- {all_daily[-1][0].date()}, {len(all_daily)} trading days")

    c = simulate_combines(all_daily, haircut=HAIRCUT)
    x = simulate_xfa_chain(all_daily, haircut=HAIRCUT)

    net_total = float(x["net_payouts"])
    accounts = x["accounts"]
    mean_per_acct = net_total / accounts if accounts else 0.0

    print(f"\nCOMBINE: attempts {c['attempts']} | passes {c['passes']} | busts {c['busts']} | "
          f"median days-to-pass {c['median_days_to_pass']}")
    print(f"XFA:     accounts {x['accounts']} | busts {x['busts']} | "
          f"net ${net_total:,.0f} | mean ${mean_per_acct:,.0f}/acct")

    # Variance report without monthly cost
    v = pipeline_variance_summary(x, monthly_fixed_cost=0.0)
    print(f"\n--- B105 Variance Report (no monthly cost) ---")
    print(f"  mean   $/acct: ${mean_per_acct:,.0f}")
    print(f"  median $/acct: ${v['median_net_per_account']:,.0f}")
    print(f"  p25    $/acct: ${v['p25_net_per_account']:,.0f}")
    print(f"  series span:   {v['total_months']} months ({x['series_start_month']} -- {x['series_end_month']})")
    print(f"  dry spells:    {len(v['dry_spells'])} run(s), max {v['max_dry_spell_months']} months")
    print(f"  dry spell list: {v['dry_spells']}")
    print(f"  reserve rec:   {v['reserve_months']} months of operating costs")

    # Variance report with $200/mo fixed cost (typical data+software)
    v200 = pipeline_variance_summary(x, monthly_fixed_cost=200.0)
    print(f"\n--- B105 Variance Report ($200/mo cost) ---")
    print(f"  net after costs: ${v200['net_after_monthly_costs']:,.0f} "
          f"(gross ${net_total:,.0f} - ${200 * v200['total_months']:,.0f} costs)")
    print(f"  reserve needed:  ${v200['reserve_needed']:,.0f} "
          f"({v200['reserve_months']}mo x $200/mo)")

    # Per-account distribution
    per_acct = x["per_account_net_payouts"]
    zeros = sum(1 for p in per_acct if p == 0.0)
    print(f"\n--- Per-account distribution ---")
    print(f"  n={len(per_acct)} total accounts | {zeros} immediate busts ($0) | "
          f"{len(per_acct)-zeros} with some payouts")
    if per_acct:
        sorted_pa = sorted(per_acct)
        print(f"  min=${sorted_pa[0]:,.0f} | max=${sorted_pa[-1]:,.0f}")

    # Monthly payout schedule
    monthly = x["monthly_net_payouts"]
    all_months = v["dry_spells"]  # already parsed
    n_zero = v["total_months"] - len([m for m in
                                      (x["monthly_net_payouts"].values()) if m > 0])
    pct_zero = 100 * n_zero / v["total_months"] if v["total_months"] else 0
    print(f"\n--- Monthly payout schedule ---")
    print(f"  {v['total_months']} calendar months | "
          f"{len(monthly)} months with payouts | "
          f"{n_zero} zero-payout months ({pct_zero:.0f}%)")


if __name__ == "__main__":
    main()
