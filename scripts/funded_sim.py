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
    daily_pnls_from_equity,
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
    args = ap.parse_args()

    haircut = Decimal(args.haircut)
    curve: list[tuple[datetime, Decimal]] = []
    with open(args.equity_csv, newline="") as f:
        for row in csv.DictReader(f):
            curve.append((datetime.fromisoformat(row["ts"]), Decimal(row["equity"])))

    daily = daily_pnls_from_equity(curve)
    c = simulate_combines(daily, haircut=haircut)
    x = simulate_xfa_chain(daily, haircut=haircut)

    haircut_note = f", haircut ${haircut:.0f}" if haircut else ""
    print(
        f"COMBINE: attempts {c['attempts']} | passes {c['passes']} | "
        f"busts {c['busts']} | median days-to-pass {c['median_days_to_pass']}\n"
        f"XFA:     accounts {x['accounts']} | busts {x['busts']} | "
        f"payouts ${x['gross_payouts']:.0f} gross / ${x['net_payouts']:.0f} net (90%)\n"
        f"(daily granularity — intraday MLL touches understated{haircut_note})"
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
