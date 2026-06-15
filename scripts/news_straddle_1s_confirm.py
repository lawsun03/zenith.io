"""1-second confirmation of the news straddle on any instrument (B91).

ATR (offset) comes from the COARSE 1-min file (needs >1h pre-event history);
the 15-min range, entry detection and management come from the 1-SECOND window
file. Same spec as scripts/news_multi.py (offset=0.5xATR, stop=range boundary
R=offset, TP=3R) but with the entry path resolved second-by-second, managing
from the second AFTER the trigger. Reports by year AND by side (long/short) so a
trend-confound (e.g. gold-FOMC in a gold bull) is visible.
"""
from __future__ import annotations
import argparse
from datetime import timedelta
import pandas as pd

OFFSET_ATR_MULT = 0.5
ATR_P = 14
RANGE_MIN = 15
ENTRY_WIN = 30
HOLD_MIN = 180
SLIP = 2
SPREAD = 1


def load(path):
    return pd.read_csv(path, parse_dates=["ts"]).set_index("ts").sort_index()


def to5(df):
    r = df.resample("5min", label="left", closed="left")
    o = pd.DataFrame({"open": r["open"].first(), "high": r["high"].max(),
                      "low": r["low"].min(), "close": r["close"].last()}).dropna()
    pc = o["close"].shift(1)
    tr = pd.concat([o["high"] - o["low"], (o["high"] - pc).abs(), (o["low"] - pc).abs()], axis=1).max(axis=1)
    o["atr"] = tr.rolling(ATR_P).mean()
    return o


def simulate(s1, b5, ts, tick):
    slip = SLIP * tick; hs = SPREAD / 2 * tick
    pre = b5[b5.index + timedelta(minutes=5) <= ts]
    atr = float(pre["atr"].iloc[-1]) if len(pre) and pd.notna(pre["atr"].iloc[-1]) else None
    if not atr or atr <= 0:
        return None
    off = OFFSET_ATR_MULT * atr
    rng = s1.loc[ts - timedelta(minutes=RANGE_MIN): ts - timedelta(seconds=1)]
    if len(rng) < 60:
        return None
    rhigh, rlow = float(rng["high"].max()), float(rng["low"].min())
    buy, sell = rhigh + off, rlow - off
    win = s1.loc[ts: ts + timedelta(minutes=ENTRY_WIN)]
    side = ""; entry = stop = tp = None; fts = None
    for t, bar in win.iterrows():
        up, dn = bar["high"] >= buy, bar["low"] <= sell
        if up and dn:
            return ("whipsaw", -1.0, "long")
        if up:
            side = "long"; entry = buy + slip + hs; stop = rhigh; tp = entry + 3 * (entry - stop); fts = t; break
        if dn:
            side = "short"; entry = sell - slip - hs; stop = rlow; tp = entry - 3 * (stop - entry); fts = t; break
    if not side:
        return ("nofill", 0.0, "")
    ru = abs(entry - stop)
    hold = s1.loc[fts + timedelta(seconds=1): fts + timedelta(minutes=HOLD_MIN)]
    for _, bar in hold.iterrows():
        if side == "long":
            if bar["low"] <= stop: return ("stop", ((stop - slip - hs) - entry) / ru, side)
            if bar["high"] >= tp: return ("tp", ((tp - hs) - entry) / ru, side)
        else:
            if bar["high"] >= stop: return ("stop", (entry - (stop + slip + hs)) / ru, side)
            if bar["low"] <= tp: return ("tp", (entry - (tp + hs)) / ru, side)
    last = float(hold["close"].iloc[-1]) if len(hold) else entry
    return ("timeout", ((last - entry) if side == "long" else (entry - last)) / ru, side)


def _stat(rs):
    vals = [r for _, r, _ in rs]
    if not vals: return "n=0"
    w = [v for v in vals if v > 0]; gw = sum(w); gl = -sum(v for v in vals if v < 0)
    pf = gw / gl if gl > 0 else 99.9
    yr = pd.DataFrame([(y, r) for y, r, _ in rs], columns=["y", "r"]).groupby("y")["r"].sum()
    return f"n={len(vals):>3} win={len(w)/len(vals)*100:>3.0f}% PF={pf:>5.2f} R/trd={sum(vals)/len(vals):>+6.3f} yrs+={int((yr>0).sum())}/{len(yr)}"


def run(coarse, s1path, tick, event_type, label):
    b5 = to5(load(coarse)); s1 = load(s1path)
    ev = pd.read_csv("data/news_events.csv", parse_dates=["ts_utc"])
    ev = ev[(ev.event_type == event_type) & (ev.ts_utc.dt.year != 2022)
            & (ev.ts_utc >= s1.index[0]) & (ev.ts_utc <= s1.index[-1])]
    rows = []
    for _, e in ev.iterrows():
        r = simulate(s1, b5, e.ts_utc, tick)
        if r and r[0] != "nofill":
            rows.append((e.ts_utc.year, r[1], r[2]))
    print(f"\n{label} {event_type} 1s:  ALL    {_stat(rows)}")
    for sd in ("long", "short"):
        print(f"{' '*len(label)} {event_type} 1s:  {sd:<6} {_stat([r for r in rows if r[2] == sd])}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--coarse", required=True); ap.add_argument("--s1", required=True)
    ap.add_argument("--tick", type=float, required=True); ap.add_argument("--event-type", required=True)
    ap.add_argument("--label", required=True)
    a = ap.parse_args()
    run(a.coarse, a.s1, a.tick, a.event_type, a.label)
