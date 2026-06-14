"""Straddle + Fade across instruments, ATR-normalized so the breakout distance is
comparable (a fixed 60-tick offset is 0.07% on NQ but 0.3% on ES / tiny on oil).

offset = OFFSET_ATR_MULT * ATR(14, 5-min pre-event)  (~= 60 ticks on NQ, so the NQ
run here should reproduce the confirmed fixed-60t straddle -- that is the self-check).
STRADDLE: 1-min, range[event-15m,event), entry range±offset, stop=range boundary
(R=offset), TP=3R, manage from entry (entry bar judged by close). FADE: 5-min, fade
the first-15m impulse on a reversal-close, stop=impulse extreme, TP=mean-revert-to-ref.
Both: by event type, 2022 excluded, no lookahead, 2-tick slip + half-spread.
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


def load1(path):
    return pd.read_csv(path, parse_dates=["ts"]).set_index("ts").sort_index()


def to5(df):
    r = df.resample("5min", label="left", closed="left")
    o = pd.DataFrame({"open": r["open"].first(), "high": r["high"].max(),
                      "low": r["low"].min(), "close": r["close"].last()}).dropna()
    pc = o["close"].shift(1)
    tr = pd.concat([o["high"] - o["low"], (o["high"] - pc).abs(), (o["low"] - pc).abs()], axis=1).max(axis=1)
    o["atr"] = tr.rolling(ATR_P).mean()
    return o


def atr_at(b5, ts):
    pre = b5[b5.index + timedelta(minutes=5) <= ts]
    return float(pre["atr"].iloc[-1]) if len(pre) and pd.notna(pre["atr"].iloc[-1]) else None


def straddle(df, b5, ts, tick):
    slip = SLIP * tick; hs = SPREAD / 2 * tick
    atr = atr_at(b5, ts)
    if not atr or atr <= 0:
        return None
    off = OFFSET_ATR_MULT * atr
    rng = df.loc[ts - timedelta(minutes=RANGE_MIN): ts - timedelta(minutes=1)]
    if len(rng) < RANGE_MIN - 2:
        return None
    rhigh, rlow = float(rng["high"].max()), float(rng["low"].min())
    buy, sell = rhigh + off, rlow - off
    win = df.loc[ts: ts + timedelta(minutes=ENTRY_WIN)]
    side = ""; entry = stop = tp = None; fts = None
    for t, bar in win.iterrows():
        up, dn = bar["high"] >= buy, bar["low"] <= sell
        if up and dn:
            return -1.0
        if up:
            side = "long"; entry = max(buy, bar["open"]) + slip + hs; stop = rhigh; tp = entry + 3 * (entry - stop); fts = t; break
        if dn:
            side = "short"; entry = min(sell, bar["open"]) - slip - hs; stop = rlow; tp = entry - 3 * (stop - entry); fts = t; break
    if not side:
        return None
    ru = abs(entry - stop)
    hold = df.loc[fts: fts + timedelta(minutes=HOLD_MIN)]
    for k, (_, bar) in enumerate(hold.iterrows()):
        eb = k == 0
        if side == "long":
            hs_ = (bar["close"] <= stop) if eb else (bar["low"] <= stop)
            ht = (bar["close"] >= tp) if eb else (bar["high"] >= tp)
            if hs_: return ((stop - slip - hs) - entry) / ru
            if ht: return ((tp - hs) - entry) / ru
        else:
            hs_ = (bar["close"] >= stop) if eb else (bar["high"] >= stop)
            ht = (bar["close"] <= tp) if eb else (bar["low"] <= tp)
            if hs_: return (entry - (stop + slip + hs)) / ru
            if ht: return (entry - (tp + hs)) / ru
    last = float(hold["close"].iloc[-1])
    return ((last - entry) if side == "long" else (entry - last)) / ru


def fade(b5, ts, tick):
    slip = SLIP * tick; hs = SPREAD / 2 * tick
    pre = b5.loc[: ts - timedelta(minutes=1)]
    if pre.empty: return None
    ref = float(pre["close"].iloc[-1])
    imp = b5.loc[ts: ts + timedelta(minutes=14)]
    if len(imp) < 2: return None
    ih, il = float(imp["high"].max()), float(imp["low"].min())
    up = (ih - ref) >= (ref - il); extreme = ih if up else il
    scan = b5.loc[ts + timedelta(minutes=15): ts + timedelta(minutes=60)]
    prev = imp.iloc[-1]; side = ""; entry = stop = None; fts = None
    for t, bar in scan.iterrows():
        if up and bar["close"] < prev["low"]:
            side = "short"; entry = bar["close"] - slip - hs; stop = extreme + slip + hs; fts = t; break
        if (not up) and bar["close"] > prev["high"]:
            side = "long"; entry = bar["close"] + slip + hs; stop = extreme - slip - hs; fts = t; break
        prev = bar
    if not side: return None
    ru = abs(entry - stop)
    if ru <= 0: return None
    tp = ref  # mean-revert to pre-news
    hold = b5.loc[fts + timedelta(minutes=5): fts + timedelta(minutes=HOLD_MIN)]
    for _, bar in hold.iterrows():
        if side == "long":
            if bar["low"] <= stop: return ((stop - slip - hs) - entry) / ru
            if bar["high"] >= tp: return ((tp - hs) - entry) / ru
        else:
            if bar["high"] >= stop: return (entry - (stop + slip + hs)) / ru
            if bar["low"] <= tp: return (entry - (tp + hs)) / ru
    last = float(hold["close"].iloc[-1]) if len(hold) else entry
    return ((last - entry) if side == "long" else (entry - last)) / ru


def stats(rs):
    vals = [v for _, v in rs]
    if not vals: return None
    w = [v for v in vals if v > 0]; gw = sum(w); gl = -sum(v for v in vals if v < 0)
    pf = gw / gl if gl > 0 else 99.9
    yr = pd.DataFrame(rs, columns=["y", "r"]).groupby("y")["r"].sum()
    return len(vals), len(w) / len(vals) * 100, pf, sum(vals) / len(vals), int((yr > 0).sum()), len(yr)


def run(bars, tick, label):
    df = load1(bars); b5 = to5(df)
    ev = pd.read_csv("data/news_events.csv", parse_dates=["ts_utc"])
    ev = ev[(ev.ts_utc >= df.index[0] + timedelta(hours=3)) & (ev.ts_utc <= df.index[-1] - timedelta(hours=3))
            & (ev.ts_utc.dt.year != 2022)]
    print(f"\n######## {label}  (tick={tick}, ATR-offset {OFFSET_ATR_MULT}xATR, {df.index[0].date()}..{df.index[-1].date()}) ########")
    for strat, fn, kind in (("STRADDLE 3R", straddle, "s"), ("FADE->ref", fade, "f")):
        print(f"-- {strat}")
        print(f"   {'event':<6}{'n':>4}{'win%':>6}{'PF':>7}{'R/trd':>8}{'yrs+':>6}")
        for et in ("CPI", "PPI", "FOMC"):
            rs = []
            for _, e in ev[ev.event_type == et].iterrows():
                r = (straddle(df, b5, e.ts_utc, tick) if kind == "s" else fade(b5, e.ts_utc, tick))
                if r is not None:
                    rs.append((e.ts_utc.year, r))
            s = stats(rs)
            if s:
                print(f"   {et:<6}{s[0]:>4}{s[1]:>6.0f}{s[2]:>7.2f}{s[3]:>+8.3f}{f'{s[4]}/{s[5]}':>6}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", required=True); ap.add_argument("--tick", type=float, required=True)
    ap.add_argument("--label", required=True)
    a = ap.parse_args(); run(a.bars, a.tick, a.label)
