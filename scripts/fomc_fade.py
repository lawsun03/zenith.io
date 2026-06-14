"""FOMC news-FADE (counter-trend) -- raw profitability first-look.

Screenshot playbook: (1) let the algos clear -- skip the first minute after the
14:00 ET statement; (2) wait for the initial spike to exhaust over the first
~15 min; (3) enter COUNTER to the initial move on a reversal confirmation;
(4) hard stop just beyond the initial news-candle extreme.

Mechanization (one clean reading; flagged where discretionary):
- ref = close of the last 5-min bar before the event.
- Impulse window = first 3 five-min bars [E, E+15). Impulse direction = whichever
  excursion from ref is larger (up-spike -> fade SHORT; down-spike -> fade LONG).
  Extreme = impulse high (up) / low (down).
- Reversal entry: in (E+15, E+60], first 5-min bar that CLOSES back through the
  prior bar's counter extreme (short: close < prior.low; long: close > prior.high).
  Enter at that close.
- Stop = impulse extreme +/- buffer (R = |entry - stop|).  TP tested: 1R, 2R, and
  "mean-revert to ref" (the pre-news price).  Manage to MAX_HOLD or give back.
- Costs: slip + half-spread adverse on entry/stop; 2022 excluded; no lookahead.
"""
from __future__ import annotations
from datetime import timedelta
import pandas as pd

TICK = 0.25
IMPULSE_MIN = 15
REVERSAL_WINDOW_MIN = 60
MAX_HOLD_MIN = 180
STOP_BUF_TICKS = 2
SLIP_TICKS = 2
SPREAD_TICKS = 1


def load5(path):
    df = pd.read_csv(path, parse_dates=["ts"]).set_index("ts").sort_index()
    r = df.resample("5min", label="left", closed="left")
    return pd.DataFrame({"open": r["open"].first(), "high": r["high"].max(),
                         "low": r["low"].min(), "close": r["close"].last()}).dropna()


def simulate(b5, ts, tp_mode):
    slip = SLIP_TICKS * TICK; hs = (SPREAD_TICKS / 2) * TICK; buf = STOP_BUF_TICKS * TICK
    pre = b5.loc[: ts - timedelta(minutes=1)]
    if pre.empty:
        return None
    ref = float(pre["close"].iloc[-1])
    imp = b5.loc[ts: ts + timedelta(minutes=IMPULSE_MIN - 1)]
    if len(imp) < 2:
        return None
    ihigh, ilow = float(imp["high"].max()), float(imp["low"].min())
    up = (ihigh - ref) >= (ref - ilow)          # up-spike -> fade short
    extreme = ihigh if up else ilow

    scan = b5.loc[ts + timedelta(minutes=IMPULSE_MIN): ts + timedelta(minutes=REVERSAL_WINDOW_MIN)]
    if len(scan) < 2:
        return ("noentry", 0.0)
    prev = imp.iloc[-1]
    entry = stop = tp = None; side = ""; fill_ts = None
    for tstamp, bar in scan.iterrows():
        if up and bar["close"] < prev["low"]:    # reversal down confirmed -> short
            side = "short"; entry = bar["close"] - slip - hs; stop = extreme + buf + slip + hs
            fill_ts = tstamp; break
        if (not up) and bar["close"] > prev["high"]:  # reversal up -> long
            side = "long"; entry = bar["close"] + slip + hs; stop = extreme - buf - slip - hs
            fill_ts = tstamp; break
        prev = bar
    if not side:
        return ("noentry", 0.0)

    r_unit = abs(entry - stop)
    if r_unit <= 0:
        return ("noentry", 0.0)
    if tp_mode == "ref":
        tp = ref
    else:
        rr = float(tp_mode)
        tp = entry + rr * r_unit * (1 if side == "long" else -1)

    hold = b5.loc[fill_ts + timedelta(minutes=5): fill_ts + timedelta(minutes=MAX_HOLD_MIN)]
    for _, bar in hold.iterrows():
        if side == "long":
            if bar["low"] <= stop:  return ("stop", ((stop - slip - hs) - entry) / r_unit)
            if bar["high"] >= tp:   return ("tp", ((tp - hs) - entry) / r_unit)
        else:
            if bar["high"] >= stop: return ("stop", (entry - (stop + slip + hs)) / r_unit)
            if bar["low"] <= tp:    return ("tp", (entry - (tp + hs)) / r_unit)
    last = float(hold["close"].iloc[-1]) if len(hold) else entry
    return ("timeout", ((last - entry) if side == "long" else (entry - last)) / r_unit)


def run():
    b5 = load5("bars/bars_MNQ_dbv_2021_2026.csv")
    b0, b1 = b5.index[0], b5.index[-1]
    ev = pd.read_csv("data/news_events.csv", parse_dates=["ts_utc"])
    ev = ev[(ev.ts_utc >= b0 + timedelta(hours=2))
            & (ev.ts_utc <= b1 - timedelta(hours=2)) & (ev.ts_utc.dt.year != 2022)]
    for etype in ("FOMC", "CPI", "PPI"):
      sub = ev[ev.event_type == etype]
      print(f"\n===== {etype} fade -- {len(sub)} events (2022 excluded) =====")
      print(f"{'TP':>6}{'n':>4}{'entry%':>8}{'win%':>6}{'PF':>7}{'R/trd':>8}{'totR':>8}{'yrs+':>6}")
      for tp_mode in ("1", "2", "ref"):
        rs = []
        for _, e in sub.iterrows():
            r = simulate(b5, e.ts_utc, tp_mode)
            if r:
                rs.append((e.ts_utc.year, r[0], r[1]))
        traded = [(y, o, v) for (y, o, v) in rs if o != "noentry"]
        if not traded:
            print(f"{tp_mode:>6}{len(rs):>4}{'0':>8}"); continue
        vals = [v for _, _, v in traded]
        wins = [v for v in vals if v > 0]
        gw = sum(v for v in vals if v > 0); gl = -sum(v for v in vals if v < 0)
        pf = gw / gl if gl > 0 else 99.9
        yr = pd.DataFrame([(y, v) for y, _, v in traded], columns=["y", "r"]).groupby("y")["r"].sum()
        print(f"{tp_mode:>6}{len(rs):>4}{len(traded)/len(rs)*100:>8.0f}{len(wins)/len(vals)*100:>6.0f}"
              f"{pf:>7.2f}{sum(vals)/len(vals):>8.3f}{sum(vals):>8.1f}{f'{int((yr>0).sum())}/{len(yr)}':>6}")


if __name__ == "__main__":
    run()
