"""News straddle -- Lawrence's exact transcript spec (raw profitability check).

Rules (per the transcript):
- Mark the 15-min pre-news RANGE: high/low over [event-15min, event).
- Entries: buy_stop = range_high + 60 ticks, sell_stop = range_low - 60 ticks (OCO).
- First leg touched after the event fills; the other is cancelled.
- Stop-loss = the broken range boundary (long->range_high, short->range_low), so
  R = 60 ticks (15 pts). [This is the one ambiguous part of the transcript; it is
  the tightest sensible reading and the only one that makes 3-4R reachable.]
- Take-profit = TP_R x R, tested at 3R and 4R.
- Costs: adverse slippage on stop fills (swept), 1-tick spread.
- Same 1-min bar spans both legs OR hits stop+TP -> resolved as a loss/stop
  (never assume the favorable order; 1-min is the finest data).
- Frozen holdout: calendar 2022 excluded. No lookahead (range + stops set from
  data <= event; only the scheduled event time is forward).

Breakeven win rate: 3R -> 25%, 4R -> 20% (before slippage).
"""
from __future__ import annotations
import argparse
from datetime import timedelta
import pandas as pd

TICK = 0.25
OFFSET_TICKS = 60
RANGE_MIN = 15
ENTRY_WINDOW_MIN = 30
MAX_HOLD_MIN = 180
SPREAD_TICKS = 1.0


def load_bars(path):
    return pd.read_csv(path, parse_dates=["ts"]).set_index("ts").sort_index()


def simulate(df, ts, tp_r, slip_ticks):
    offset = OFFSET_TICKS * TICK
    slip = slip_ticks * TICK
    half_spread = (SPREAD_TICKS / 2.0) * TICK

    rng = df.loc[ts - timedelta(minutes=RANGE_MIN): ts - timedelta(minutes=1)]
    if len(rng) < RANGE_MIN - 2:
        return None
    rhigh, rlow = float(rng["high"].max()), float(rng["low"].min())
    buy_stop, sell_stop = rhigh + offset, rlow - offset

    win = df.loc[ts: ts + timedelta(minutes=ENTRY_WINDOW_MIN)]
    if win.empty:
        return None

    side = ""; entry = stop_level = tp_level = None; fill_ts = None; whip = False
    for tstamp, bar in win.iterrows():
        up, dn = bar["high"] >= buy_stop, bar["low"] <= sell_stop
        if up and dn:                       # both legs same bar -> whipsaw stop-out
            whip = True; side = "long"
            entry = buy_stop + slip + half_spread; stop_level = rhigh
            r_unit = entry - stop_level
            exit_px = rhigh - slip - half_spread
            return ("whipsaw", (exit_px - entry) / r_unit, side)
        if up:
            side = "long"; base = max(buy_stop, bar["open"])
            entry = base + slip + half_spread; stop_level = rhigh
            tp_level = entry + tp_r * (entry - stop_level); fill_ts = tstamp; break
        if dn:
            side = "short"; base = min(sell_stop, bar["open"])
            entry = base - slip - half_spread; stop_level = rlow
            tp_level = entry - tp_r * (stop_level - entry); fill_ts = tstamp; break
    if not side:
        return ("nofill", 0.0, "")

    r_unit = abs(entry - stop_level)
    # Middle assumption: include the entry bar but judge it by its CLOSE (a clear
    # intra-minute reversal that ENDS beyond the stop counts; the entry bar's
    # raw low/high is not used, since it spans the pre-entry trigger move). All
    # subsequent bars use high/low normally.
    hold = df.loc[fill_ts: fill_ts + timedelta(minutes=MAX_HOLD_MIN)]
    for k, (_, bar) in enumerate(hold.iterrows()):
        entry_bar = (k == 0)
        if side == "long":
            hit_stop = (bar["close"] <= stop_level) if entry_bar else (bar["low"] <= stop_level)
            hit_tp = (bar["close"] >= tp_level) if entry_bar else (bar["high"] >= tp_level)
            stop_px, tp_px = stop_level - slip - half_spread, tp_level - half_spread
        else:
            hit_stop = (bar["close"] >= stop_level) if entry_bar else (bar["high"] >= stop_level)
            hit_tp = (bar["close"] <= tp_level) if entry_bar else (bar["low"] <= tp_level)
            stop_px, tp_px = stop_level + slip + half_spread, tp_level + half_spread
        if hit_stop:                         # stop checked first (conservative)
            return ("stop", ((stop_px - entry) if side == "long" else (entry - stop_px)) / r_unit, side)
        if hit_tp:
            return ("tp", ((tp_px - entry) if side == "long" else (entry - tp_px)) / r_unit, side)
    last = float(hold["close"].iloc[-1])
    pnl = (last - half_spread - entry) if side == "long" else (entry - last - half_spread)
    return ("timeout", pnl / r_unit, side)


def run(bars_path, events_path):
    df = load_bars(bars_path)
    b0, b1 = df.index[0], df.index[-1]
    ev = pd.read_csv(events_path, parse_dates=["ts_utc"])
    ev = ev[(ev.ts_utc >= b0 + timedelta(hours=2)) & (ev.ts_utc <= b1 - timedelta(hours=2))
            & (ev.ts_utc.dt.year != 2022)]
    detail = []  # per-event notes at the headline config (TP=3R, slip=2)
    for tp_r in (3.0, 4.0):
        for slip in (2.0, 4.0):
            rows = []
            for _, e in ev.iterrows():
                r = simulate(df, e.ts_utc, tp_r, slip)
                if r:
                    rows.append((e.event_type, *r))
                    if tp_r == 3.0 and slip == 2.0:
                        detail.append({"date": e.ts_utc.date().isoformat(), "type": e.event_type,
                                       "side": r[2], "outcome": r[0], "R": round(r[1], 2)})
            print(f"\n===== TP={tp_r:.0f}R  slip={slip:.0f}t   (breakeven WR={100/(1+tp_r):.0f}%) =====")
            print(f"{'grp':<6}{'n':>4}{'win%':>6}{'whip%':>6}{'tp':>4}{'stop':>5}{'R/trd':>8}{'totR':>8}{'PF':>6}")
            for grp in ("CPI", "PPI", "FOMC", "ALL"):
                rs = [x for x in rows if (grp == "ALL" or x[0] == grp)]
                filled = [x for x in rs if x[1] != "nofill"]
                if not filled:
                    continue
                rmults = [x[2] for x in filled]
                wins = [r for r in rmults if r > 0]
                gw = sum(r for r in rmults if r > 0); gl = -sum(r for r in rmults if r < 0)
                pf = (gw / gl) if gl > 0 else float("inf")
                ntp = sum(1 for x in filled if x[1] == "tp")
                nstop = sum(1 for x in filled if x[1] in ("stop", "whipsaw"))
                nwhip = sum(1 for x in filled if x[1] == "whipsaw")
                print(f"{grp:<6}{len(filled):>4}{len(wins)/len(filled)*100:>6.0f}"
                      f"{nwhip/len(filled)*100:>6.0f}{ntp:>4}{nstop:>5}"
                      f"{sum(rmults)/len(rmults):>8.3f}{sum(rmults):>8.1f}{pf:>6.2f}")


    # --- per-event notes + per-type yearly breakdown at headline config ---
    det = pd.DataFrame(detail)
    det.to_csv("research/news_straddle_userspec_events.csv", index=False)
    print("\n\n########## PER-EVENT NOTES (TP=3R, slip=2t) ##########")
    for grp in ("CPI", "PPI", "FOMC"):
        g = det[det["type"] == grp]
        print(f"\n----- {grp}: {len(g)} events  |  win% {(g.R>0).mean()*100:.0f}  |  totR {g.R.sum():+.1f}  |  by year:")
        yr = g.assign(year=pd.to_datetime(g.date).dt.year).groupby("year").agg(
            n=("R", "size"), winpct=("R", lambda s: round((s > 0).mean() * 100)), totR=("R", "sum"))
        print(yr.to_string())
        print(f"  events ->", ", ".join(f"{r.date}({r.outcome[:1].upper()}{r.R:+.1f})" for r in g.itertuples()))
    print("\nfull per-event CSV -> research/news_straddle_userspec_events.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", default="bars/bars_MNQ_dbv_2021_2026.csv")
    ap.add_argument("--events", default="data/news_events.csv")
    a = ap.parse_args()
    run(a.bars, a.events)
