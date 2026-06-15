"""Scratch: does NQ move enough intraday to reach the ORB 2.5R target?
Quick INTUITION sim on 1-min bars (NOT the official backtester): 9:30-9:45 OR,
first 1-min close beyond OR after 9:45 = entry, stop = opposite OR boundary,
target = 2.5R, walk to 16:00 ET. Same-bar stop+target => count as stop (conservative).
"""
import pandas as pd

F = "bars/bars_MNQ_dbv_2021_2026.csv"
RR = 2.5
df = pd.read_csv(F)
df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("America/New_York")
df = df.set_index("ts").sort_index()
df["d"] = df.index.date
df["hm"] = df.index.strftime("%H:%M")

rth = df.between_time("09:30", "15:59")
# Daily RTH range context
g = rth.groupby("d")
rng = (g["high"].max() - g["low"].min()).dropna()
print(f"NQ daily RTH range (points), n={len(rng)} days:")
print(f"  median {rng.median():.0f} | p25 {rng.quantile(.25):.0f} | p75 {rng.quantile(.75):.0f} | max {rng.max():.0f}")
for thr in (200, 300, 447, 600):
    print(f"  % days range >= {thr}pt: {100*(rng>=thr).mean():.0f}%")

wins=losses=flats=0; rs=[]; tgt_pts=[]
for d, day in rth.groupby("d"):
    orb = day.between_time("09:30", "09:44")
    if len(orb) < 10:
        continue
    oh, ol = orb["high"].max(), orb["low"].min()
    after = day.between_time("09:45", "15:59")
    entry=stop=tgt=side=None; etime=None
    for ts, b in after.iterrows():
        if b["close"] > oh:
            side, entry, stop = "L", b["close"], ol
            tgt = entry + RR*(entry-stop); etime=ts; break
        if b["close"] < ol:
            side, entry, stop = "S", b["close"], oh
            tgt = entry - RR*(stop-entry); etime=ts; break
    if entry is None:
        continue
    tgt_pts.append(abs(tgt-entry))
    seg = after[after.index > etime]
    outcome=None
    for ts, b in seg.iterrows():
        if side=="L":
            hit_stop = b["low"]<=stop; hit_tgt = b["high"]>=tgt
        else:
            hit_stop = b["high"]>=stop; hit_tgt = b["low"]<=tgt
        if hit_stop: outcome=("stop", -1.0); break       # conservative: stop wins ties
        if hit_tgt: outcome=("tgt", RR); break
    if outcome is None:
        last = seg["close"].iloc[-1] if len(seg) else entry
        r = (last-entry)/(entry-stop) if side=="L" else (entry-last)/(stop-entry)
        outcome=("flat", r)
    kind, r = outcome; rs.append(r)
    if kind=="tgt": wins+=1
    elif kind=="stop": losses+=1
    else: flats+=1

n=wins+losses+flats
import statistics as st
print(f"\nORB 2.5R sim, n={n} trade-days:")
print(f"  target HIT (2.5R win): {wins} ({100*wins/n:.0f}%)")
print(f"  stopped out (-1R):     {losses} ({100*losses/n:.0f}%)")
print(f"  EOD flat (partial R):  {flats} ({100*flats/n:.0f}%)")
print(f"  mean R/trade: {st.mean(rs):+.3f}   median target distance: {st.median(tgt_pts):.0f}pt")
