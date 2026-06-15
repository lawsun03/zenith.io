"""Supply/Demand zone retest -- Phase 1 raw profitability (NO engine).

Mechanizes the transcript's method (deterministic, 15-min bars, MNQ 5y, 2022 excl):
- ZONE = an order-block base that price leaves RAPIDLY (Law 1 momentum):
  demand = a down/`base` candle immediately followed by IMPULSE_BARS strong
  BULLISH bars (each body >= BODY_K*ATR) with net departure >= DEPART_K*ATR.
  Zone region = [base.low, base.high]; proximal=base.high, distal=base.low.
  Supply = mirror (base up-candle + strong bearish impulse).
- FRESHNESS (Law 2): a zone is valid from formation until its FIRST touch, and
  expires after FRESH_DAYS. Only the first retest is traded (first_touch_only).
- ENTRY on retest: demand -> price trades back down into the zone; enter LONG at
  proximal (limit). Stop = distal -/+ BUF beyond the zone. R = |entry-stop|.
  TP = tp_r * R. Manage forward up to HOLD_DAYS. (Law 3 confluence intentionally
  OFF -- this measures the RAW zone edge first.)
- Costs: limit entry at proximal (no adverse slip on the resting limit); stop/TP
  market exits pay SLIP+half-spread. No lookahead (zone uses only bars <= form).
"""
from __future__ import annotations
from datetime import timedelta
import pandas as pd

TICK = 0.25
TF = "15min"
ATR_P = 14
IMPULSE_BARS = 3
BODY_K = 0.5
DEPART_K = 1.5
FRESH_DAYS = 5
HOLD_DAYS = 3
BUF_TICKS = 4
SLIP_TICKS = 2
SPREAD_TICKS = 1


def load_tf(path):
    df = pd.read_csv(path, parse_dates=["ts"]).set_index("ts").sort_index()
    r = df.resample(TF, label="left", closed="left")
    o = pd.DataFrame({"open": r["open"].first(), "high": r["high"].max(),
                      "low": r["low"].min(), "close": r["close"].last()}).dropna()
    pc = o["close"].shift(1)
    tr = pd.concat([o["high"] - o["low"], (o["high"] - pc).abs(), (o["low"] - pc).abs()], axis=1).max(axis=1)
    o["atr"] = tr.rolling(ATR_P).mean()
    return o


def detect_zones(b, body_k=BODY_K, depart_k=DEPART_K):
    """Return list of (form_idx, side, proximal, distal). side: 'demand'|'supply'."""
    zones = []
    body = (b["close"] - b["open"]).abs()
    for i in range(ATR_P + 1, len(b) - IMPULSE_BARS - 1):
        atr = b["atr"].iloc[i]
        if not atr or atr <= 0:
            continue
        base = b.iloc[i]
        imp = b.iloc[i + 1:i + 1 + IMPULSE_BARS]
        if base["close"] <= base["open"]:
            bull = (imp["close"] > imp["open"]).all()
            strong = (body.iloc[i + 1:i + 1 + IMPULSE_BARS] >= body_k * atr).all()
            depart = (imp["close"].iloc[-1] - base["high"]) >= depart_k * atr
            if bull and strong and depart:
                zones.append((i, "demand", float(base["high"]), float(base["low"])))
                continue
        if base["close"] >= base["open"]:
            bear = (imp["close"] < imp["open"]).all()
            strong = (body.iloc[i + 1:i + 1 + IMPULSE_BARS] >= body_k * atr).all()
            depart = (base["low"] - imp["close"].iloc[-1]) >= depart_k * atr
            if bear and strong and depart:
                zones.append((i, "supply", float(base["low"]), float(base["high"])))
    return zones


def simulate(b, zones, tp_r):
    slip = SLIP_TICKS * TICK; hs = (SPREAD_TICKS / 2) * TICK; buf = BUF_TICKS * TICK
    out = []
    idx = b.index
    for (fi, side, prox, dist) in zones:
        form_ts = idx[fi]
        fresh_end = form_ts + timedelta(days=FRESH_DAYS)
        # first retest after the impulse, within freshness window
        scan = b.iloc[fi + 1 + IMPULSE_BARS:]
        scan = scan[scan.index <= fresh_end]
        entry_ts = None
        for ts, bar in scan.iterrows():
            if side == "demand" and bar["low"] <= prox:
                entry_ts = ts; break
            if side == "supply" and bar["high"] >= prox:
                entry_ts = ts; break
        if entry_ts is None:
            continue
        if side == "demand":
            entry = prox; stop = dist - buf; tp = entry + tp_r * (entry - stop)
        else:
            entry = prox; stop = dist + buf; tp = entry - tp_r * (stop - entry)
        r_unit = abs(entry - stop)
        if r_unit <= 0:
            continue
        hold = b.loc[entry_ts: entry_ts + timedelta(days=HOLD_DAYS)]
        res = None
        for ts, bar in hold.iterrows():
            if ts == entry_ts:
                continue  # entry bar already touched the zone to fill us
            if side == "demand":
                if bar["low"] <= stop: res = ((stop - slip - hs) - entry) / r_unit; break
                if bar["high"] >= tp: res = ((tp - hs) - entry) / r_unit; break
            else:
                if bar["high"] >= stop: res = (entry - (stop + slip + hs)) / r_unit; break
                if bar["low"] <= tp: res = (entry - (tp + hs)) / r_unit; break
        if res is None:
            last = float(hold["close"].iloc[-1])
            res = ((last - entry) if side == "demand" else (entry - last)) / r_unit
        out.append((entry_ts.year, side, res))
    return out


def run(bars_path="bars/bars_MNQ_dbv_2021_2026.csv", keep_2022=False, dump=None):
    b = load_tf(bars_path)
    if not keep_2022:
        b = b[b.index.year != 2022]
    zones = detect_zones(b)
    nd = sum(1 for z in zones if z[1] == "demand"); ns = len(zones) - nd
    print(f"zones detected: {len(zones)} ({nd} demand / {ns} supply) over {b.index[0].date()}..{b.index[-1].date()}\n")
    print(f"{'tp_r':>5}{'side':>8}{'n':>5}{'win%':>6}{'PF':>7}{'R/trd':>8}{'totR':>8}{'/yr':>6}{'yrs+':>6}")
    yrs_span = b.index.year.nunique()
    for tp_r in (2.0, 3.0):
        res = simulate(b, zones, tp_r)
        if dump and tp_r == 3.0:
            pd.DataFrame(res, columns=["year", "side", "r"]).to_csv(dump, index=False)
            print(f"  (per-trade dump @3R -> {dump})")
        for side in ("demand", "supply", "ALL"):
            rs = [r for r in res if side == "ALL" or r[1] == side]
            if not rs:
                continue
            vals = [v for _, _, v in rs]
            wins = [v for v in vals if v > 0]
            gw = sum(v for v in vals if v > 0); gl = -sum(v for v in vals if v < 0)
            pf = gw / gl if gl > 0 else 99.9
            yr = pd.DataFrame([(y, v) for y, _, v in rs], columns=["y", "r"]).groupby("y")["r"].sum()
            print(f"{tp_r:>5.0f}{side:>8}{len(vals):>5}{len(wins)/len(vals)*100:>6.0f}{pf:>7.2f}"
                  f"{sum(vals)/len(vals):>8.3f}{sum(vals):>8.1f}{len(vals)/yrs_span:>6.0f}{f'{int((yr>0).sum())}/{len(yr)}':>6}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", default="bars/bars_MNQ_dbv_2021_2026.csv")
    ap.add_argument("--tick", type=float, default=0.25)  # MNQ .25, MGC .10, MCL .01
    ap.add_argument("--keep2022", action="store_true")
    ap.add_argument("--dump", default=None)
    a = ap.parse_args()
    TICK = a.tick
    print(f"instrument bars: {a.bars}  | tick={TICK}  | keep2022={a.keep2022}")
    run(a.bars, keep_2022=a.keep2022, dump=a.dump)
