"""CPI straddle replayed on 1-SECOND bars (B85 entry-bar resolution).

Same spec as scripts/news_straddle_cpi_sweep.py (15-min pre-range, entries at
range_high+offset / range_low-offset, TIGHT stop at the broken range boundary
[R=offset], TP = tp_r * R) but on 1-second bars over the CPI windows, managing
from the second AFTER the trigger -- so the intra-minute "spike -> tag stop ->
recover" path that 1-min could not see is now resolved to ~1s. Compares directly
to the 1-min estimate (offset 60: 3R 63% win PF 5.0 +1.51R; 4R 59% PF 5.57 +1.92R).
"""
from __future__ import annotations
from datetime import timedelta
import pandas as pd

TICK = 0.25
RANGE_MIN = 15
ENTRY_WINDOW_MIN = 30
MAX_HOLD_MIN = 180
SPREAD_TICKS = 1
SLIP_TICKS = 2


def load_1s(path):
    return pd.read_csv(path, parse_dates=["ts"]).set_index("ts").sort_index()


def simulate(df, ts, offset_ticks, tp_r):
    offset = offset_ticks * TICK; slip = SLIP_TICKS * TICK; hs = (SPREAD_TICKS / 2) * TICK
    rng = df.loc[ts - timedelta(minutes=RANGE_MIN): ts - timedelta(seconds=1)]
    if len(rng) < 60:                       # need real pre-range coverage in 1s bars
        return None
    rhigh, rlow = float(rng["high"].max()), float(rng["low"].min())
    buy, sell = rhigh + offset, rlow - offset
    win = df.loc[ts: ts + timedelta(minutes=ENTRY_WINDOW_MIN)]
    if win.empty:
        return None
    side = ""; entry = stop = tp = None; fill_ts = None
    for tstamp, bar in win.iterrows():
        up, dn = bar["high"] >= buy, bar["low"] <= sell
        if up and dn:                       # both legs in ONE second -> whipsaw -1R
            return ("whipsaw", -1.0)
        if up:
            side = "long"; entry = buy + slip + hs; stop = rhigh
            tp = entry + tp_r * (entry - stop); fill_ts = tstamp; break
        if dn:
            side = "short"; entry = sell - slip - hs; stop = rlow
            tp = entry - tp_r * (stop - entry); fill_ts = tstamp; break
    if not side:
        return ("nofill", 0.0)
    r_unit = abs(entry - stop)
    hold = df.loc[fill_ts + timedelta(seconds=1): fill_ts + timedelta(minutes=MAX_HOLD_MIN)]
    for _, bar in hold.iterrows():
        if side == "long":
            if bar["low"] <= stop: return ("stop", ((stop - slip - hs) - entry) / r_unit)
            if bar["high"] >= tp: return ("tp", ((tp - hs) - entry) / r_unit)
        else:
            if bar["high"] >= stop: return ("stop", (entry - (stop + slip + hs)) / r_unit)
            if bar["low"] <= tp: return ("tp", (entry - (tp + hs)) / r_unit)
    last = float(hold["close"].iloc[-1]) if len(hold) else entry
    return ("timeout", ((last - entry) if side == "long" else (entry - last)) / r_unit)


def run(bars="bars/bars_NQ_1s_cpi_windows.csv"):
    df = load_1s(bars)
    ev = pd.read_csv("data/news_events.csv", parse_dates=["ts_utc"])
    ev = ev[(ev.event_type == "CPI") & (ev.ts_utc.dt.year != 2022)
            & (ev.ts_utc >= df.index[0]) & (ev.ts_utc <= df.index[-1])]
    print(f"CPI 1s replay -- {len(ev)} events, bars {df.index[0]}..{df.index[-1]}\n")
    print(f"{'offset':>7}{'tp_r':>5}{'n':>4}{'win%':>6}{'whip%':>6}{'PF':>7}{'R/trd':>8}{'totR':>8}{'yrs+':>6}")
    for offset in (60,):
        for tp_r in (3.0, 4.0):
            rows = []
            for _, e in ev.iterrows():
                r = simulate(df, e.ts_utc, offset, tp_r)
                if r:
                    rows.append((e.ts_utc.year, r[0], r[1]))
            traded = [x for x in rows if x[1] != "nofill"]
            if not traded:
                print(f"{offset:>7}{tp_r:>5.0f}  no fills"); continue
            vals = [v for _, _, v in traded]; wins = [v for v in vals if v > 0]
            nwhip = sum(1 for _, o, _ in traded if o == "whipsaw")
            gw = sum(v for v in vals if v > 0); gl = -sum(v for v in vals if v < 0)
            pf = gw / gl if gl > 0 else 99.9
            yr = pd.DataFrame([(y, v) for y, _, v in traded], columns=["y", "r"]).groupby("y")["r"].sum()
            print(f"{offset:>7}{tp_r:>5.0f}{len(vals):>4}{len(wins)/len(vals)*100:>6.0f}"
                  f"{nwhip/len(vals)*100:>6.0f}{pf:>7.2f}{sum(vals)/len(vals):>8.3f}{sum(vals):>8.1f}"
                  f"{f'{int((yr>0).sum())}/{len(yr)}':>6}")
    print("\n1-min estimate for reference: 60/3R 63% PF 5.0 +1.51R ; 60/4R 59% PF 5.57 +1.92R")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--bars", default="bars/bars_NQ_1s_cpi_windows.csv")
    a = ap.parse_args(); run(a.bars)
