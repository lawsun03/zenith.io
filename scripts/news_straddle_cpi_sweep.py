"""CPI-only news-straddle parameter sweep (offset x stop_mode x tp_r).

Exploratory: find whether the CPI edge sharpens with a different breakout offset
or stop definition. ANTI-OVERFIT GUARD: the headline metric is years_positive/5
(robustness across years), not aggregate R — a config that only wins in aggregate
but not most years is curve-fit. Same fair entry-bar handling (close-based) and
no-lookahead contract as scripts/news_straddle_user_spec.py. 2022 excluded.

stop_mode:
  range = stop at the broken range boundary (R = offset)            [tight]
  opp   = stop at the opposite straddle leg (R = range + 2*offset)  [wide]
"""
from __future__ import annotations
from datetime import timedelta
import pandas as pd

TICK = 0.25
RANGE_MIN = 15
ENTRY_WINDOW_MIN = 30
MAX_HOLD_MIN = 180
SPREAD_TICKS = 1.0
SLIP_TICKS = 2.0


def load_bars(path):
    return pd.read_csv(path, parse_dates=["ts"]).set_index("ts").sort_index()


def simulate(df, ts, offset_ticks, stop_mode, tp_r):
    offset = offset_ticks * TICK
    slip = SLIP_TICKS * TICK
    hs = (SPREAD_TICKS / 2.0) * TICK
    rng = df.loc[ts - timedelta(minutes=RANGE_MIN): ts - timedelta(minutes=1)]
    if len(rng) < RANGE_MIN - 2:
        return None
    rhigh, rlow = float(rng["high"].max()), float(rng["low"].min())
    buy_stop, sell_stop = rhigh + offset, rlow - offset
    win = df.loc[ts: ts + timedelta(minutes=ENTRY_WINDOW_MIN)]
    if win.empty:
        return None

    side = ""; entry = stop_level = tp_level = None; fill_ts = None
    for tstamp, bar in win.iterrows():
        up, dn = bar["high"] >= buy_stop, bar["low"] <= sell_stop
        if up and dn:  # both legs same bar -> whipsaw stop-out (-1R)
            return 0.0 - 1.0  # symmetric: realize -1R
        if up:
            side = "long"; entry = max(buy_stop, bar["open"]) + slip + hs
            stop_level = rhigh if stop_mode == "range" else sell_stop
            tp_level = entry + tp_r * (entry - stop_level); fill_ts = tstamp; break
        if dn:
            side = "short"; entry = min(sell_stop, bar["open"]) - slip - hs
            stop_level = rlow if stop_mode == "range" else buy_stop
            tp_level = entry - tp_r * (stop_level - entry); fill_ts = tstamp; break
    if not side:
        return None  # no fill

    r_unit = abs(entry - stop_level)
    hold = df.loc[fill_ts: fill_ts + timedelta(minutes=MAX_HOLD_MIN)]
    for k, (_, bar) in enumerate(hold.iterrows()):
        eb = (k == 0)
        if side == "long":
            hit_stop = (bar["close"] <= stop_level) if eb else (bar["low"] <= stop_level)
            hit_tp = (bar["close"] >= tp_level) if eb else (bar["high"] >= tp_level)
            sx, tx = stop_level - slip - hs, tp_level - hs
        else:
            hit_stop = (bar["close"] >= stop_level) if eb else (bar["high"] >= stop_level)
            hit_tp = (bar["close"] <= tp_level) if eb else (bar["low"] <= tp_level)
            sx, tx = stop_level + slip + hs, tp_level + hs
        if hit_stop:
            return ((sx - entry) if side == "long" else (entry - sx)) / r_unit
        if hit_tp:
            return ((tx - entry) if side == "long" else (entry - tx)) / r_unit
    last = float(hold["close"].iloc[-1])
    pnl = (last - hs - entry) if side == "long" else (entry - last - hs)
    return pnl / r_unit


def run():
    df = load_bars("bars/bars_MNQ_dbv_2021_2026.csv")
    b0, b1 = df.index[0], df.index[-1]
    ev = pd.read_csv("data/news_events.csv", parse_dates=["ts_utc"])
    ev = ev[(ev.event_type == "CPI") & (ev.ts_utc >= b0 + timedelta(hours=2))
            & (ev.ts_utc <= b1 - timedelta(hours=2)) & (ev.ts_utc.dt.year != 2022)]
    print(f"CPI events: {len(ev)}  (2022 excluded)\n")
    print(f"{'offset':>7}{'stop':>7}{'tp_r':>5}{'n':>4}{'win%':>6}{'PF':>7}{'R/trd':>8}{'totR':>8}{'yrs+':>6}")
    for offset in (40, 60, 80):
        for stop_mode in ("range", "opp"):
            for tp_r in (2.0, 3.0, 4.0):
                rs = []
                for _, e in ev.iterrows():
                    r = simulate(df, e.ts_utc, offset, stop_mode, tp_r)
                    if r is not None:
                        rs.append((e.ts_utc.year, r))
                if not rs:
                    continue
                vals = [r for _, r in rs]
                wins = [v for v in vals if v > 0]
                gw = sum(v for v in vals if v > 0); gl = -sum(v for v in vals if v < 0)
                pf = gw / gl if gl > 0 else 99.9
                yrs = pd.DataFrame(rs, columns=["y", "r"]).groupby("y")["r"].sum()
                yrs_pos = int((yrs > 0).sum()); yrs_n = len(yrs)
                print(f"{offset:>7}{stop_mode:>7}{tp_r:>5.0f}{len(vals):>4}{len(wins)/len(vals)*100:>6.0f}"
                      f"{pf:>7.2f}{sum(vals)/len(vals):>8.3f}{sum(vals):>8.1f}{f'{yrs_pos}/{yrs_n}':>6}")


if __name__ == "__main__":
    run()
