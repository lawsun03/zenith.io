"""
B53 Phase 1 PnL analysis: iFVG N+1 adversity early exit gate.

For each not-confirmed trade (N+1 bar closes adversely), compute the early-exit
P&L at N+1 close and compare to held-to-stop P&L.

Scale factor per trade: $/pt * contracts = realized_pnl / directional_price_diff
  Long:  scale = realized_pnl / (exit_price - entry_price)  -- both same sign
  Short: scale = realized_pnl / (entry_price - exit_price)  -- both same sign

Early exit P&L (always <= 0 since N+1 is adverse by definition):
  Long:  early_pnl = (nb_close - entry_price) * scale   (< 0, nb_close < entry)
  Short: early_pnl = (entry_price - nb_close) * scale   (< 0, nb_close > entry)

GO/NO-GO (both required):
  1. Early exit reduces not-confirmed net losses by >= 30%
  2. Early exit improves aggregate PF by >= 10% vs baseline
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import csv
from datetime import datetime, timedelta

IFVG_CSV = ROOT / "research" / "mfe_mae_ifvg_clean.csv"
BARS_CSV = ROOT / "bars" / "bars_MNQ_dbv_2021_2026.csv"
EXCLUDE_YEAR = 2022


def load_bars(path: Path) -> dict[str, dict]:
    bars = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bars[row["ts"]] = row
    return bars


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def next_bar_key(entry_ts: datetime) -> str:
    nb = entry_ts + timedelta(minutes=5)
    return nb.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def pf_stats(pnls: list[float]) -> dict:
    if not pnls:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "net": 0.0, "gw": 0.0, "gl": 0.0}
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gw = sum(wins)
    gl = abs(sum(losses))
    pf = gw / gl if gl > 0 else float("inf")
    wr = len(wins) / n * 100
    return {"n": n, "wr": wr, "pf": pf, "net": gw - gl, "gw": gw, "gl": gl}


def process_trades(
    trades: list[dict],
    bars: dict[str, dict],
) -> tuple[list[float], list[float], list[float], int, int]:
    """
    Returns: (confirmed_pnls, unconf_actual_pnls, unconf_early_pnls,
              no_next_count, scale_error_count)
    """
    confirmed_pnls = []
    unconf_actual_pnls = []
    unconf_early_pnls = []
    no_next = 0
    scale_errors = 0

    for t in trades:
        entry_ts = parse_ts(t["entry_ts"])
        entry_price = float(t["entry_price"])
        exit_price = float(t["exit_price"])
        side = t["side"]
        realized_pnl = float(t["realized_pnl"])

        # Directional price diff (always positive for winners, negative for losers)
        if side == "long":
            price_diff = exit_price - entry_price
        else:
            price_diff = entry_price - exit_price

        # Skip near-zero movement (BE scratches; scale undefined)
        if abs(price_diff) < 0.05:
            scale_errors += 1
            continue

        nb_key = next_bar_key(entry_ts)
        nb = bars.get(nb_key)
        if nb is None:
            no_next += 1
            continue

        nb_close = float(nb["close"])

        # Confirmed = N+1 closes with signal direction
        if side == "long":
            confirmed = nb_close > entry_price
        else:  # short
            confirmed = nb_close < entry_price

        if confirmed:
            confirmed_pnls.append(realized_pnl)
        else:
            # Scale: $/pt * contracts for this trade (always > 0)
            scale = realized_pnl / price_diff

            # Early exit at N+1 close (adverse -> always a loss)
            if side == "long":
                early_pnl = (nb_close - entry_price) * scale
            else:
                early_pnl = (entry_price - nb_close) * scale

            unconf_actual_pnls.append(realized_pnl)
            unconf_early_pnls.append(early_pnl)

    return confirmed_pnls, unconf_actual_pnls, unconf_early_pnls, no_next, scale_errors


def main():
    print("B53 Phase 1: N+1 adversity early exit — PnL analysis")
    print("Loading 5-min bars...")
    bars = load_bars(BARS_CSV)
    print(f"  Loaded {len(bars):,} bars")

    all_trades = []
    with open(IFVG_CSV, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if int(row["year"]) == EXCLUDE_YEAR:
                continue
            all_trades.append(row)
    print(f"  iFVG trades (excl {EXCLUDE_YEAR}): {len(all_trades)}")

    conf, unconf_act, unconf_early, no_next, scale_err = process_trades(all_trades, bars)
    print(f"  Missing N+1 bar: {no_next} | scale errors (BE scratches): {scale_err}")

    # Baseline (no early exit)
    baseline_all = conf + unconf_act
    bs_conf = pf_stats(conf)
    bs_unconf = pf_stats(unconf_act)
    bs_all = pf_stats(baseline_all)

    # With early exit on unconfirmed
    early_all = conf + unconf_early
    ee_unconf = pf_stats(unconf_early)
    ee_all = pf_stats(early_all)

    print("\n=== Baseline (no early exit) ===")
    print(f"{'Group':<28} {'n':>5} {'WR%':>7} {'PF':>6} {'Net$':>12} {'GW$':>12} {'GL$':>12}")
    print("-" * 80)
    for name, s in [("Confirmed (N+1 favorable)", bs_conf),
                    ("Unconfirmed (N+1 adverse)", bs_unconf),
                    ("ALL", bs_all)]:
        print(f"{name:<28} {s['n']:>5} {s['wr']:>7.1f} {s['pf']:>6.3f} {s['net']:>12,.0f} {s['gw']:>12,.0f} {s['gl']:>12,.0f}")

    print("\n=== With Early Exit on Unconfirmed Trades ===")
    print(f"{'Group':<28} {'n':>5} {'WR%':>7} {'PF':>6} {'Net$':>12} {'GW$':>12} {'GL$':>12}")
    print("-" * 80)
    for name, s in [("Confirmed (unchanged)", bs_conf),
                    ("Unconfirmed (early exit)", ee_unconf),
                    ("ALL (with early exit)", ee_all)]:
        print(f"{name:<28} {s['n']:>5} {s['wr']:>7.1f} {s['pf']:>6.3f} {s['net']:>12,.0f} {s['gw']:>12,.0f} {s['gl']:>12,.0f}")

    # Winner breakdown for not-confirmed group
    unconf_winners = [p for p in unconf_act if p > 0]
    unconf_losers = [p for p in unconf_act if p <= 0]
    early_winner_outcomes = [unconf_early[i] for i, p in enumerate(unconf_act) if p > 0]
    early_loser_outcomes = [unconf_early[i] for i, p in enumerate(unconf_act) if p <= 0]

    print(f"\n=== Not-Confirmed Sub-groups ===")
    print(f"  Actual winners (cut short by early exit): n={len(unconf_winners)}")
    if unconf_winners:
        print(f"    Avg actual win:      ${sum(unconf_winners)/len(unconf_winners):,.0f}")
        print(f"    Avg early exit pnl:  ${sum(early_winner_outcomes)/len(early_winner_outcomes):,.0f}")
        print(f"    Gross cost of cutting winners: ${sum(early_winner_outcomes) - sum(unconf_winners):,.0f}")
    print(f"  Actual losers (capped by early exit):    n={len(unconf_losers)}")
    if unconf_losers:
        print(f"    Avg actual loss:     ${sum(unconf_losers)/len(unconf_losers):,.0f}")
        print(f"    Avg early exit pnl:  ${sum(early_loser_outcomes)/len(early_loser_outcomes):,.0f}")
        print(f"    Gross savings on losers: ${sum(early_loser_outcomes) - sum(unconf_losers):,.0f}")

    # GO/NO-GO
    print("\n=== GO/NO-GO Criteria ===")
    unconf_net_actual = bs_unconf["net"]
    unconf_net_early = ee_unconf["net"]

    if unconf_net_actual < 0 and unconf_net_early > unconf_net_actual:
        loss_reduction_pct = (unconf_net_early - unconf_net_actual) / abs(unconf_net_actual) * 100
    else:
        loss_reduction_pct = (unconf_net_early - unconf_net_actual) / max(abs(unconf_net_actual), 1) * 100

    pf_improvement_pct = 0.0
    if bs_all["pf"] > 0 and bs_all["pf"] < float("inf"):
        pf_improvement_pct = (ee_all["pf"] / bs_all["pf"] - 1) * 100

    crit1 = loss_reduction_pct >= 30.0
    crit2 = pf_improvement_pct >= 10.0

    print(f"Crit 1 — Not-confirmed loss reduction >= 30%:")
    print(f"  Current not-confirmed net: ${unconf_net_actual:,.0f}")
    print(f"  Early exit not-confirmed net: ${unconf_net_early:,.0f}")
    print(f"  Reduction: {loss_reduction_pct:.1f}% -- {'PASS' if crit1 else 'FAIL'}")
    print(f"Crit 2 -- Aggregate PF improvement >= 10%:")
    print(f"  Baseline all-trades PF:    {bs_all['pf']:.4f}")
    print(f"  Early exit all-trades PF:  {ee_all['pf']:.4f}")
    print(f"  Improvement: {pf_improvement_pct:.1f}% -- {'PASS' if crit2 else 'FAIL'}")
    print(f"\nPhase 1 verdict: {'GO -- proceed to Phase 2 build' if (crit1 and crit2) else 'NO-GO -- do not build Phase 2'}")

    # Per-year breakdown
    print("\n=== Per-year (not-confirmed: actual vs early exit) ===")
    for year in [2021, 2023, 2024, 2025, 2026]:
        yr_trades = [t for t in all_trades if int(t["year"]) == year]
        yr_c, yr_ua, yr_ue, _, _ = process_trades(yr_trades, bars)
        a = pf_stats(yr_ua)
        e = pf_stats(yr_ue)
        all_a = pf_stats(yr_c + yr_ua)
        all_e = pf_stats(yr_c + yr_ue)
        print(f"  {year}: unconf actual PF={a['pf']:.3f} net={a['net']:>10,.0f} | "
              f"early PF={e['pf']:.3f} net={e['net']:>10,.0f} | "
              f"agg PF: {all_a['pf']:.3f}->{all_e['pf']:.3f} (n={a['n']})")


if __name__ == "__main__":
    main()
