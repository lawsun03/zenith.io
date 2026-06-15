"""CLI: funded-pipeline sim over a backtest equity CSV (header: ts,equity)."""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
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
    simulate_combines,
    simulate_xfa_chain,
)


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
    c = simulate_combines(daily, haircut=haircut)
    x = simulate_xfa_chain(daily, haircut=haircut, combine_gap_days=args.combine_gap_days)

    haircut_note = f", haircut ${haircut:.0f}" if haircut else ""
    dll_note = f", DLL ${dll_amount:.0f}" if dll_amount else ""
    print(
        f"COMBINE: attempts {c['attempts']} | passes {c['passes']} | "
        f"busts {c['busts']} | median days-to-pass {c['median_days_to_pass']}\n"
        f"XFA:     accounts {x['accounts']} | busts {x['busts']} | "
        f"payouts ${x['gross_payouts']:.0f} gross / ${x['net_payouts']:.0f} net (90%)\n"
        f"(daily granularity — intraday MLL touches understated{haircut_note}{dll_note})"
    )

    if args.bootstrap > 0:
        print(f"\nBootstrap CIs ({args.bootstrap} resamples, block={args.block_len}d):")
        bs = bootstrap_pipeline(
            daily,
            n_resamples=args.bootstrap,
            block_len=args.block_len,
            haircut=haircut,
            combine_gap_days=args.combine_gap_days,
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
