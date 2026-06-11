"""CLI: funded-pipeline sim over a backtest equity CSV (header: ts,equity)."""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.funded_sim import format_pipeline_summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("equity_csv")
    ap.add_argument("--haircut", default="0",
                    help="Assumed intraday adverse excursion in $ below each "
                         "day's close when checking MLL touches (default 0)")
    args = ap.parse_args()
    curve = []
    with open(args.equity_csv, newline="") as f:
        for row in csv.DictReader(f):
            curve.append((datetime.fromisoformat(row["ts"]), Decimal(row["equity"])))
    print(format_pipeline_summary(curve, haircut=Decimal(args.haircut)))


if __name__ == "__main__":
    main()
