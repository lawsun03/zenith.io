"""Day-of-week PF analysis on MFE/MAE data for wk2-r1 research session."""
import csv
import datetime
from collections import defaultdict


def analyze_dow(filename: str, label: str) -> None:
    dow_wins: dict[str, float] = defaultdict(float)
    dow_losses: dict[str, float] = defaultdict(float)
    dow_n: dict[str, int] = defaultdict(int)

    with open(filename, newline="") as f:
        for row in csv.DictReader(f):
            ts = datetime.datetime.fromisoformat(row["entry_ts"])
            dow = ts.strftime("%A")
            pnl = float(row["realized_pnl"])
            dow_n[dow] += 1
            if pnl > 0:
                dow_wins[dow] += pnl
            else:
                dow_losses[dow] += abs(pnl)

    print(f"\n=== {label} DOW breakdown ===")
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    for d in order:
        n = dow_n.get(d, 0)
        gw = dow_wins.get(d, 0.0)
        gl = dow_losses.get(d, 0.0)
        pf = round(gw / gl, 3) if gl > 0 else float("inf")
        net = round(gw - gl, 0)
        print(f"  {d}: n={n} PF={pf} net=${net:,.0f}")


if __name__ == "__main__":
    analyze_dow("research/mfe_mae_ifvg_clean.csv", "iFVG 5y (excl 2022)")
    analyze_dow("research/mfe_mae_orb_clean.csv", "ORB 5y (excl 2022)")
