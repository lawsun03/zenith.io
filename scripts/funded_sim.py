"""CLI: funded-pipeline sim over a backtest equity CSV (header: ts,equity)."""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.funded_sim import (
    bootstrap_pipeline,
    cap_daily_pnls_at_dll,
    daily_pnls_from_equity,
    daily_pnls_with_low,
    pipeline_variance_summary,
    simulate_combines,
    simulate_xfa_chain,
)
from app.risk.account_phase import CombineRules, XfaRules


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("equity_csv")
    ap.add_argument("--haircut", default="0",
                    help="Assumed intraday adverse excursion in $ below each "
                         "day's close when checking MLL touches (default 0)")
    ap.add_argument("--save-id", default=None,
                    help="Write a UI-visible result JSON to backtests/<id>.json")
    ap.add_argument("--save-label", default=None,
                    help="Display label for the saved result")
    ap.add_argument("--instrument", default="MNQ",
                    help="Instrument label for the registry entry (default MNQ)")
    ap.add_argument("--timeframe", default="5min",
                    help="Timeframe label for the registry entry (default 5min)")
    ap.add_argument("--combine-gap-days", type=int, default=0,
                    help="Trading days skipped after each XFA bust before starting the "
                         "next account (models the real combine re-attempt gap; "
                         "default 0 = existing behavior)")
    ap.add_argument("--bootstrap", type=int, default=0, metavar="N",
                    help="Run N-resample block-bootstrap to produce 5/25/50/75/95 "
                         "CIs on xfa_net, xfa_busts, combine_passes (default 0 = off). "
                         "Use 1000 for publication; 100 for a quick check.")
    ap.add_argument("--block-len", type=int, default=20, metavar="DAYS",
                    help="Block length in trading days for the bootstrap (default 20 = ~1 month). "
                         "Must be shorter than the series length.")
    ap.add_argument("--dll", default="0", metavar="DOLLARS",
                    help="Daily loss limit in $ to apply per day before MLL check "
                         "(e.g. 500 for 1%% of a $50K funded account). "
                         "0 = disabled (default, existing behavior). "
                         "When triggered intraday, caps the day P&L at -DLL.")
    # B104 firm-rule shock flags — override individual XfaRules/CombineRules fields.
    # Omit a flag to use the dataclass default. Used for counterparty shock grid.
    ap.add_argument("--payout-cap", default=None, metavar="DOLLARS",
                    help="Override XfaRules.payout_cap (default 2000). "
                         "e.g. 5000 to back-test the pre-2026-04-28 rule.")
    ap.add_argument("--profit-share", default=None, metavar="FRAC",
                    help="Override XfaRules.trader_profit_share fraction (default 0.90).")
    ap.add_argument("--xfa-mll-distance", default=None, metavar="DOLLARS",
                    help="Override XfaRules.mll_distance in $ (default 2000).")
    ap.add_argument("--combine-mll-distance", default=None, metavar="DOLLARS",
                    help="Override CombineRules.mll_distance in $ (default 2000).")
    # B105: recurring monthly fixed cost (software, data, subscriptions) that
    # reduces true net return. Subtracted from every calendar month in the window.
    ap.add_argument("--monthly-cost", default="0", metavar="DOLLARS",
                    help="Recurring monthly fixed cost to subtract from every month "
                         "in the backtest window (incl. idle months with no payouts). "
                         "Yields net_after_costs for honest comparison. Default 0.")
    args = ap.parse_args()

    haircut = Decimal(args.haircut)
    dll_amount = Decimal(args.dll)
    curve: list[tuple[datetime, Decimal]] = []
    with open(args.equity_csv, newline="") as f:
        for row in csv.DictReader(f):
            curve.append((datetime.fromisoformat(row["ts"]), Decimal(row["equity"])))

    if dll_amount > 0:
        daily_extended = daily_pnls_with_low(curve)
        daily = cap_daily_pnls_at_dll(daily_extended, dll_amount)
    else:
        daily = daily_pnls_from_equity(curve)

    # Build firm-rule overrides (B104). Replace only the fields that were explicitly
    # set; leave the rest at dataclass defaults so omitted flags are transparent.
    xfa_rules = XfaRules()
    combine_rules = CombineRules()
    if args.payout_cap is not None:
        xfa_rules = replace(xfa_rules, payout_cap=Decimal(args.payout_cap))
    if args.profit_share is not None:
        xfa_rules = replace(xfa_rules, trader_profit_share=Decimal(args.profit_share))
    if args.xfa_mll_distance is not None:
        xfa_rules = replace(xfa_rules, mll_distance=Decimal(args.xfa_mll_distance))
    if args.combine_mll_distance is not None:
        combine_rules = replace(combine_rules, mll_distance=Decimal(args.combine_mll_distance))

    monthly_cost = float(args.monthly_cost)

    c = simulate_combines(daily, rules=combine_rules, haircut=haircut)
    x = simulate_xfa_chain(daily, rules=xfa_rules, haircut=haircut, combine_gap_days=args.combine_gap_days)

    haircut_note = f", haircut ${haircut:.0f}" if haircut else ""
    dll_note = f", DLL ${dll_amount:.0f}" if dll_amount else ""
    rule_notes = []
    if args.payout_cap is not None:
        rule_notes.append(f"payout_cap=${args.payout_cap}")
    if args.profit_share is not None:
        rule_notes.append(f"profit_share={args.profit_share}")
    if args.xfa_mll_distance is not None:
        rule_notes.append(f"xfa_mll=${args.xfa_mll_distance}")
    if args.combine_mll_distance is not None:
        rule_notes.append(f"combine_mll=${args.combine_mll_distance}")
    rule_note = (", " + " ".join(rule_notes)) if rule_notes else ""
    print(
        f"COMBINE: attempts {c['attempts']} | passes {c['passes']} | "
        f"busts {c['busts']} | median days-to-pass {c['median_days_to_pass']}\n"
        f"XFA:     accounts {x['accounts']} | busts {x['busts']} | "
        f"payouts ${x['gross_payouts']:.0f} gross / ${x['net_payouts']:.0f} net (90%)\n"
        f"(daily granularity -- intraday MLL touches understated{haircut_note}{dll_note}{rule_note})"
    )

    # B105: variance report
    v = pipeline_variance_summary(x, monthly_fixed_cost=monthly_cost)
    per_acct_n = x["accounts"]
    mean_net = float(x["net_payouts"]) / per_acct_n if per_acct_n else 0.0
    print(
        f"VARIANCE: mean ${mean_net:,.0f}/acct | "
        f"median ${v['median_net_per_account']:,.0f}/acct | "
        f"p25 ${v['p25_net_per_account']:,.0f}/acct\n"
        f"          dry spells: {len(v['dry_spells'])} runs | "
        f"max {v['max_dry_spell_months']}mo | "
        f"recommend {v['reserve_months']}mo cash reserve"
        + (
            f" (${v['reserve_needed']:,.0f} @ ${monthly_cost:.0f}/mo)"
            if monthly_cost else ""
        )
    )
    if monthly_cost:
        print(
            f"          net after ${monthly_cost:.0f}/mo x {v['total_months']}mo costs: "
            f"${v['net_after_monthly_costs']:,.0f}"
        )

    if args.bootstrap > 0:
        print(f"\nBootstrap CIs ({args.bootstrap} resamples, block={args.block_len}d):")
        bs = bootstrap_pipeline(
            daily,
            n_resamples=args.bootstrap,
            block_len=args.block_len,
            haircut=haircut,
            combine_gap_days=args.combine_gap_days,
            xfa_rules=xfa_rules,
            combine_rules=combine_rules,
        )
        for metric, cis in [
            ("xfa_net ($)", bs["xfa_net"]),
            ("xfa_busts   ", bs["xfa_busts"]),
            ("comb_passes  ", bs["combine_passes"]),
        ]:
            print(
                f"  {metric}: "
                f"p5={cis[5]:.0f}  p25={cis[25]:.0f}  p50={cis[50]:.0f}"
                f"  p75={cis[75]:.0f}  p95={cis[95]:.0f}"
            )

    if args.save_id:
        _save_funded_result(Path(args.equity_csv), curve, args, c, x, haircut)


def _save_funded_result(
    equity_csv: Path,
    equity_curve: list[tuple[datetime, Decimal]],
    args,
    combine_stats: dict,
    xfa_stats: dict,
    haircut: Decimal,
    _out_dir: Path | None = None,
) -> None:
    """Write a backtests/<id>.json the BacktestsPage can list and open.

    The funded_pipeline block includes both combine stats and xfa stats so the
    detail view renders the full pipeline summary alongside the equity curve.
    """
    run_id = re.sub(r"[^A-Za-z0-9_\-]", "_", args.save_id)[:64]
    starting = equity_curve[0][1] if equity_curve else Decimal("0")
    ending = equity_curve[-1][1] if equity_curve else Decimal("0")
    net = ending - starting
    curve_pts = [[ts.isoformat(), str(eq)] for ts, eq in equity_curve]
    label = args.save_label or (
        f"Funded pipeline: {equity_csv.stem}"
        + (f" h{int(haircut)}" if haircut else "")
    )
    now = datetime.now(timezone.utc).isoformat()
    data = {
        "id": run_id,
        "label": label,
        "instrument": args.instrument.upper(),
        "timeframe": args.timeframe,
        "start_date": equity_curve[0][0].strftime("%Y-%m") if equity_curve else None,
        "end_date": equity_curve[-1][0].strftime("%Y-%m") if equity_curve else None,
        "bars_path": str(equity_csv),
        "bars_processed": len(equity_curve),
        "starting_balance": str(starting),
        "ending_balance": str(ending),
        "duration_seconds": 0,
        "started_at": now,
        "completed_at": now,
        "stats": {
            "trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "net_pnl": str(net),
            "gross_win": "0", "gross_loss": "0", "avg_win": "0", "avg_loss": "0",
            "profit_factor": None,
            "max_drawdown": "0",
            "expectancy": "0",
            "is_profitable": net > 0,
            "equity_curve": curve_pts,
        },
        "funded_pipeline": {
            "combine": {
                "attempts": combine_stats["attempts"],
                "passes": combine_stats["passes"],
                "busts": combine_stats["busts"],
                "median_days_to_pass": combine_stats["median_days_to_pass"],
            },
            "xfa": {
                "accounts": xfa_stats["accounts"],
                "busts": xfa_stats["busts"],
                "gross_payouts": str(xfa_stats["gross_payouts"]),
                "net_payouts": str(xfa_stats["net_payouts"]),
                "median_days_to_first_payout": xfa_stats["median_days_to_first_payout"],
            },
            "caveat": (
                "daily granularity — intraday MLL touches understated"
                + (f", haircut ${haircut:.0f}" if haircut else "")
            ),
        },
        "monthly_combine": None,
        "trades": [],
        "signals": [],
        "fills": [],
    }
    out_dir = _out_dir if _out_dir is not None else Path("backtests")
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{run_id}.json"
    out.write_text(json.dumps(data, indent=2))
    print(f"saved UI result -> {out}")


if __name__ == "__main__":
    main()
