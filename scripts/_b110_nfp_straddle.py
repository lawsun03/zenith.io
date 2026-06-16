"""B110 Phase-1: NFP 8:30 ET straddle on 1-min MNQ bars.

Methodology: identical to news_straddle_cpi_1s.py (tight stop at range boundary,
R = offset, tp_r sweep over 1/2/3) but using 1-min bars and NFP event dates.

Success gate (B110 spec):
  PF >= 3.0 at tp_r=3.0
  whipsaw_rate <= 30%
  positive (net R > 0) in >= 4/5 tested years
  n_filled >= 25

NFP dates: first Friday of each month (with known exceptions for BLS processing
delays). Vol-expansion diagnostic confirms real events (expansion >= 1.5 * ATR5).
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from pathlib import Path

import pandas as pd

TICK = 0.25
RANGE_MIN = 15           # 15-min pre-release range window
ENTRY_WINDOW_MIN = 30    # arm/entry window after release
MAX_HOLD_MIN = 180       # max hold time
SPREAD_TICKS = 1.0
SLIP_TICKS = 2.0
ATR_PERIOD = 14
OFFSET_ATR_MULT = 0.5
EXPANSION_K = 1.5        # vol expansion threshold for "confirmed" subset

# Hardcoded actual BLS NFP release dates (Employment Situation Summary).
# Source: BLS published calendar + known exceptions.
# 2022 excluded (frozen holdout). 2026 limited to Jun (bars end Jun 11).
_NFP_DATES: list[tuple[int, int, int]] = [
    # 2021 H2
    (2021,  7,  2), (2021,  8,  6), (2021,  9,  3),
    (2021, 10,  8),  # delayed from Oct 1 (BLS IT/fiscal-year issue)
    (2021, 11,  5), (2021, 12,  3),
    # 2023
    (2023,  1,  6), (2023,  2,  3),
    (2023,  3, 10),  # delayed from Mar 3
    (2023,  4,  7), (2023,  5,  5), (2023,  6,  2),
    (2023,  7,  7), (2023,  8,  4), (2023,  9,  1),
    (2023, 10,  6), (2023, 11,  3),
    (2023, 12,  8),  # delayed from Dec 1
    # 2024
    (2024,  1,  5), (2024,  2,  2),
    (2024,  3,  8),  # delayed from Mar 1
    (2024,  4,  5), (2024,  5,  3), (2024,  6,  7),
    (2024,  7,  5), (2024,  8,  2), (2024,  9,  6),
    (2024, 10,  4), (2024, 11,  1), (2024, 12,  6),
    # 2025
    (2025,  1, 10),  # delayed from Jan 3
    (2025,  2,  7), (2025,  3,  7), (2025,  4,  4),
    (2025,  5,  2), (2025,  6,  6),
    (2025,  7,  3),  # Thu release (Jul 4 holiday falls on Fri)
    (2025,  8,  1), (2025,  9,  5), (2025, 10,  3),
    (2025, 11,  7), (2025, 12,  5),
    # 2026 (through Jun, bars end Jun 11)
    (2026,  1,  9), (2026,  2,  6), (2026,  3,  6),
    (2026,  4,  3),
    (2026,  5,  8),  # delayed from May 1
    (2026,  6,  5),
]


def _is_dst(d: datetime.date) -> bool:
    """True if date d falls in US EDT (DST) period."""
    year = d.year
    sundays_mar = [
        datetime.date(year, 3, 1) + datetime.timedelta(n)
        for n in range(31)
        if (datetime.date(year, 3, 1) + datetime.timedelta(n)).weekday() == 6
    ]
    dst_start = sundays_mar[1]  # 2nd Sunday of March
    for n in range(7):
        cand = datetime.date(year, 11, 1) + datetime.timedelta(n)
        if cand.weekday() == 6:
            dst_end = cand
            break
    return dst_start <= d < dst_end


def _nfp_utc_times() -> list[datetime.datetime]:
    """Convert hardcoded NFP date tuples to UTC datetimes (8:30 AM ET)."""
    results = []
    for year, month, day in _NFP_DATES:
        release_date = datetime.date(year, month, day)
        # Note: Jul 3 2025 was a Thursday release (holiday exception)
        # 8:30 AM ET -> UTC
        hour_utc = 12 if _is_dst(release_date) else 13
        dt_utc = datetime.datetime(year, month, day, hour_utc, 30,
                                   tzinfo=datetime.timezone.utc)
        results.append(dt_utc)
    return sorted(results)


def _atr5(df: pd.DataFrame, placement: datetime.datetime) -> float | None:
    """5-min ATR(14) from 1-min bars, fully closed before placement."""
    lo = placement - datetime.timedelta(minutes=5 * (ATR_PERIOD + 2) + 10)
    win = df.loc[lo:placement]
    if win.empty:
        return None
    h = win["high"].resample("5min", label="left", closed="left").max()
    l = win["low"].resample("5min", label="left", closed="left").min()
    c = win["close"].resample("5min", label="left", closed="left").last()
    bars5 = pd.DataFrame({"high": h, "low": l, "close": c}).dropna()
    # Keep only bars whose close time <= placement
    bars5 = bars5[bars5.index + datetime.timedelta(minutes=5) <= placement]
    if len(bars5) < ATR_PERIOD + 1:
        return None
    bars5 = bars5.iloc[-(ATR_PERIOD + 1):]
    prev_c = bars5["close"].shift(1)
    tr = pd.concat([
        bars5["high"] - bars5["low"],
        (bars5["high"] - prev_c).abs(),
        (bars5["low"] - prev_c).abs(),
    ], axis=1).max(axis=1)
    return float(tr.iloc[1:].mean())


def _simulate(df: pd.DataFrame, ts: datetime.datetime, tp_r: float) -> dict | None:
    """Simulate one NFP straddle event using tight-stop methodology.

    Stop = range boundary (rhigh for long, rlow for short).
    R = entry - stop ≈ offset + slip + half_spread.
    Modelled after news_straddle_cpi_1s.py (tight R = offset unit).
    """
    slip = SLIP_TICKS * TICK
    hs = (SPREAD_TICKS / 2) * TICK
    placement = ts - datetime.timedelta(minutes=2)  # PLACE_LEAD_MIN = 2

    atr5 = _atr5(df, placement)
    if atr5 is None or atr5 <= 0:
        return None
    offset = OFFSET_ATR_MULT * atr5

    # Pre-release range (15-min window closing at placement)
    rng = df.loc[placement - datetime.timedelta(minutes=RANGE_MIN):
                 placement - datetime.timedelta(minutes=1)]
    if len(rng) < RANGE_MIN - 2:
        return None
    rhigh = float(rng["high"].max())
    rlow = float(rng["low"].min())

    buy_stop = rhigh + offset
    sell_stop = rlow - offset

    # Vol expansion diagnostic (post-release window)
    exp_win = df.loc[ts: ts + datetime.timedelta(minutes=ENTRY_WINDOW_MIN)]
    expansion = 0.0
    if not exp_win.empty:
        expansion = float(exp_win["high"].max() - exp_win["low"].min()) / atr5

    # Entry window from placement
    win = df.loc[placement: placement + datetime.timedelta(minutes=ENTRY_WINDOW_MIN)]
    if win.empty:
        return {"ts": ts, "year": ts.year, "atr5": atr5, "offset": offset,
                "filled": False, "side": "", "whipsaw": False,
                "outcome": "nofill", "r_mult": 0.0, "expansion": expansion}

    side = ""; entry = stop_level = tp_level = None; fill_idx = None

    for i in range(len(win)):
        bar = win.iloc[i]
        hi, lo = float(bar["high"]), float(bar["low"])
        op = float(bar["open"])
        up = hi >= buy_stop
        dn = lo <= sell_stop

        if up and dn:
            # Both legs in same bar: whipsaw - enter long then stop at sell_stop
            fill_entry = max(buy_stop, op) + slip + hs
            fill_stop = sell_stop
            r_unit = fill_entry - fill_stop
            exit_px = sell_stop - slip - hs
            r_mult = (exit_px - fill_entry) / r_unit if r_unit > 0 else -1.0
            return {"ts": ts, "year": ts.year, "atr5": atr5, "offset": offset,
                    "filled": True, "side": "long", "whipsaw": True,
                    "outcome": "whipsaw", "r_mult": float(r_mult), "expansion": expansion}
        if up:
            side = "long"
            fill_entry = max(buy_stop, op) + slip + hs
            stop_level = rhigh          # tight stop: range top
            tp_level = fill_entry + tp_r * (fill_entry - stop_level)
            fill_idx = i; entry = fill_entry; break
        if dn:
            side = "short"
            fill_entry = min(sell_stop, op) - slip - hs
            stop_level = rlow           # tight stop: range bottom
            tp_level = fill_entry - tp_r * (stop_level - fill_entry)
            fill_idx = i; entry = fill_entry; break

    if side == "" or fill_idx is None:
        return {"ts": ts, "year": ts.year, "atr5": atr5, "offset": offset,
                "filled": False, "side": "", "whipsaw": False,
                "outcome": "nofill", "r_mult": 0.0, "expansion": expansion}

    fill_ts = win.index[fill_idx]
    hold = df.loc[fill_ts: fill_ts + datetime.timedelta(minutes=MAX_HOLD_MIN)]
    r_unit = abs(entry - stop_level)
    outcome = "timeout"; exit_px = None

    for _, bar in hold.iterrows():
        hi, lo = float(bar["high"]), float(bar["low"])
        if side == "long":
            hit_stop = lo <= stop_level
            hit_tp = hi >= tp_level
        else:
            hit_stop = hi >= stop_level
            hit_tp = lo <= tp_level
        # Conservative: if both in same bar, take stop first
        if hit_stop:
            outcome = "stop"
            exit_px = ((stop_level - slip - hs) if side == "long"
                       else (stop_level + slip + hs))
            break
        if hit_tp:
            outcome = "tp"
            exit_px = ((tp_level - hs) if side == "long" else (tp_level + hs))
            break

    if exit_px is None:
        # Timeout: mark to last close in hold window
        if len(hold):
            last = float(hold["close"].iloc[-1])
            exit_px = last - hs if side == "long" else last + hs
        else:
            exit_px = entry

    pnl = (exit_px - entry) if side == "long" else (entry - exit_px)
    r_mult = pnl / r_unit if r_unit > 0 else 0.0

    return {"ts": ts, "year": ts.year, "atr5": atr5, "offset": offset,
            "filled": True, "side": side, "whipsaw": False,
            "outcome": outcome, "r_mult": float(r_mult), "expansion": expansion}


def _stats(rows: list[dict]) -> dict:
    filled = [r for r in rows if r["filled"]]
    whips = [r for r in rows if r.get("whipsaw")]
    if not filled:
        return {"n": 0}
    traded_r = [r["r_mult"] for r in filled]
    wins = [v for v in traded_r if v > 0]
    gw = sum(v for v in traded_r if v > 0)
    gl = -sum(v for v in traded_r if v < 0)
    pf = gw / gl if gl > 0 else 99.9
    yr_r: dict[int, float] = defaultdict(float)
    for r in filled:
        yr_r[r["year"]] += r["r_mult"]
    yrs_pos = sum(1 for v in yr_r.values() if v > 0)
    return {
        "n": len(filled),
        "n_events": len(rows),
        "fill_rate": len(filled) / len(rows),
        "whip_rate": len(whips) / len(filled) if filled else 0,
        "win_rate": len(wins) / len(filled),
        "pf": pf,
        "r_per_trade": sum(traded_r) / len(traded_r),
        "total_r": sum(traded_r),
        "yrs_pos": yrs_pos,
        "n_years": len(yr_r),
        "yr_r": dict(yr_r),
    }


def _print_stats(stats: dict, label: str, tp_r: float) -> None:
    if stats["n"] == 0:
        print(f"  {label}: no fills")
        return
    print(f"  {label:28s}  n={stats['n']:3d}  fill={stats['fill_rate']*100:4.0f}%"
          f"  whip={stats['whip_rate']*100:4.0f}%  win={stats['win_rate']*100:4.0f}%"
          f"  PF={stats['pf']:5.2f}  R/trd={stats['r_per_trade']:+.3f}"
          f"  totR={stats['total_r']:+6.1f}  yrs+={stats['yrs_pos']}/{stats['n_years']}")


def run(bars_path: str = "bars/bars_MNQ_dbv_2021_2026.csv") -> None:
    df = pd.read_csv(bars_path, parse_dates=["ts"]).set_index("ts").sort_index()
    bar_start, bar_end = df.index[0], df.index[-1]
    print(f"Bars: {bar_start} to {bar_end}")

    nfp_times = _nfp_utc_times()
    # Filter to bar coverage (need 2h margin for pre-range)
    nfp_times = [t for t in nfp_times
                 if bar_start + datetime.timedelta(hours=2) <= t
                 <= bar_end - datetime.timedelta(hours=3)]

    print(f"\nNFP event dates compiled: {len(nfp_times)}")
    for t in nfp_times:
        print(f"  {t.strftime('%Y-%m-%d %H:%MZ')}  (UTC offset: {'EDT' if t.hour == 12 else 'EST'})")

    print("\n" + "="*70)
    print("B110 NFP Straddle Phase-1 (tight stop = range boundary)")
    print("="*70)

    for tp_r in [1.0, 2.0, 3.0]:
        print(f"\n--- tp_r = {tp_r:.0f}R ---")
        results: list[dict] = []
        for ts in nfp_times:
            r = _simulate(df, ts, tp_r)
            if r is not None:
                results.append(r)

        if not results:
            print("  No results (no bar coverage)")
            continue

        all_stats = _stats(results)
        _print_stats(all_stats, "ALL events", tp_r)

        conf = [r for r in results if r.get("expansion", 0) >= EXPANSION_K]
        conf_stats = _stats(conf)
        _print_stats(conf_stats, f"exp>={EXPANSION_K:.1f} (confirmed)", tp_r)

        if tp_r == 3.0:
            # Per-year breakdown
            print(f"\n  Per-year breakdown (tp_r=3.0, ALL events):")
            yr_data: dict[int, list[dict]] = defaultdict(list)
            for r in results:
                if r["filled"]:
                    yr_data[r["year"]].append(r)
            for yr in sorted(yr_data):
                grp = yr_data[yr]
                gw = sum(r["r_mult"] for r in grp if r["r_mult"] > 0)
                gl = -sum(r["r_mult"] for r in grp if r["r_mult"] < 0)
                pf_yr = gw / gl if gl > 0 else 99.9
                wins = sum(1 for r in grp if r["r_mult"] > 0)
                tot_r = sum(r["r_mult"] for r in grp)
                print(f"    {yr}: n={len(grp):2d}  wins={wins}/{len(grp)}"
                      f"  PF={pf_yr:.2f}  totR={tot_r:+.1f}")

            # Gate check
            print(f"\n  >>> GATE CHECK (tp_r=3.0, ALL events):")
            g1 = all_stats.get("pf", 0) >= 3.0
            g2 = all_stats.get("whip_rate", 1) <= 0.30
            g3 = all_stats.get("yrs_pos", 0) >= 4
            g4 = all_stats.get("n", 0) >= 25
            n_years = all_stats.get("n_years", 0)
            print(f"    PF >= 3.0:          {all_stats.get('pf', 0):.2f} -> {'PASS' if g1 else 'FAIL'}")
            print(f"    whipsaw <= 30%:     {all_stats.get('whip_rate', 0)*100:.0f}% -> {'PASS' if g2 else 'FAIL'}")
            print(f"    yrs+ >= 4/{n_years}:     {all_stats.get('yrs_pos', 0)}/{n_years} -> {'PASS' if g3 else 'FAIL'}")
            print(f"    n_filled >= 25:     {all_stats.get('n', 0)} -> {'PASS' if g4 else 'FAIL'}")
            verdict = "PHASE-1 GO" if (g1 and g2 and g3 and g4) else "PHASE-1 NO-GO"
            print(f"\n  >>> VERDICT: {verdict}")

            # Gate check on confirmed subset
            print(f"\n  >>> GATE CHECK (tp_r=3.0, confirmed exp>={EXPANSION_K:.1f}):")
            cg1 = conf_stats.get("pf", 0) >= 3.0
            cg2 = conf_stats.get("whip_rate", 1) <= 0.30
            cg3 = conf_stats.get("yrs_pos", 0) >= 4
            cg4 = conf_stats.get("n", 0) >= 25
            cn_years = conf_stats.get("n_years", 0)
            print(f"    PF >= 3.0:          {conf_stats.get('pf', 0):.2f} -> {'PASS' if cg1 else 'FAIL'}")
            print(f"    whipsaw <= 30%:     {conf_stats.get('whip_rate', 0)*100:.0f}% -> {'PASS' if cg2 else 'FAIL'}")
            print(f"    yrs+ >= 4/{cn_years}:     {conf_stats.get('yrs_pos', 0)}/{cn_years} -> {'PASS' if cg3 else 'FAIL'}")
            print(f"    n_filled >= 25:     {conf_stats.get('n', 0)} -> {'PASS' if cg4 else 'FAIL'}")
            conf_verdict = "PHASE-1 GO" if (cg1 and cg2 and cg3 and cg4) else "PHASE-1 NO-GO"
            print(f"\n  >>> CONFIRMED VERDICT: {conf_verdict}")


if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo))

    ap = argparse.ArgumentParser(description="B110: NFP straddle Phase-1")
    ap.add_argument("--bars", default="bars/bars_MNQ_dbv_2021_2026.csv",
                    help="1-min MNQ bars CSV")
    a = ap.parse_args()
    run(a.bars)
