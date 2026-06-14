"""B62 Phase 1 — orderflow-proxy / ORB-outcome correlation (cheap falsification).

Does a bar-derived cumulative-delta proxy and/or RVOL at the ORB breakout
correlate with the trade's outcome? If there is no PF separation, do NOT build
the confirm/veto engine variants (B62 spec).

Declared FIXED proxy formulas (one each, no tunables):

  Cum-delta proxy (per 5min bar):
      delta = volume * (2*(close-low)/(high-low) - 1)        # CLV-weighted volume
      (delta = 0 when high == low). Range [-volume, +volume]; close-at-high => +vol.

  3-bar signed cum-delta at breakout (breakout bar + 2 prior 5min bars):
      cd3_signed = dir * sum(delta_3)          # dir = +1 long, -1 short
      cd3_ratio  = cd3_signed / sum(volume_3)  # in [-1, +1]; >0 confirms the breakout

  RVOL (breakout bar):
      rvol = breakout_bar_volume / mean(volume of the 5min bar at the SAME
             ET time-of-day over the prior 20 sessions)      # NaN if <20 priors

Bars are 1-min; the ORB engine runs on 5min. The recorded entry_ts is the
right-labelled close minute of the breakout 5min bar (window [label-4min, label]).
We reproduce that exact labelling so the breakout bar aligns with entry_ts.

GO/NO-GO (program convention, B45/B49): top-40% vs bottom-40% PF ratio >= 1.4x
AND consistent direction across years => GO (build the confirm gate). Else NO-GO.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parents[1]


def load_bars_5min(bars_csv: Path) -> pd.DataFrame:
    """1-min OHLCV -> 5min bars labelled at the close minute (label = bin_start+4min)."""
    df = pd.read_csv(bars_csv, parse_dates=["ts"])
    df = df.set_index("ts").sort_index()
    binned = df.resample("5min", origin="epoch", label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna(subset=["close"])
    # right-label at the close minute (bin_start covers [start, start+5min) of 1-min bars,
    # whose last included minute is start+4min — the engine's bar timestamp)
    binned.index = binned.index + pd.Timedelta(minutes=4)
    rng = (binned["high"] - binned["low"])
    clv = np.where(rng > 0, (2 * (binned["close"] - binned["low"]) / rng) - 1, 0.0)
    binned["delta"] = binned["volume"].to_numpy() * clv
    binned["tod"] = binned.index.tz_convert(ET).strftime("%H:%M")
    return binned


def pf(pnl: pd.Series) -> float:
    wins = pnl[pnl > 0].sum()
    losses = pnl[pnl < 0].sum()
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return float(wins / -losses)


def enrich(trades: pd.DataFrame, bars5: pd.DataFrame) -> pd.DataFrame:
    pos = {ts: i for i, ts in enumerate(bars5.index)}
    delta = bars5["delta"].to_numpy()
    vol = bars5["volume"].to_numpy()
    # per-TOD volume history for RVOL (sorted by ts already)
    tod_groups = {tod: g for tod, g in bars5.groupby("tod")}

    rows = []
    miss = 0
    for r in trades.itertuples(index=False):
        ets = pd.Timestamp(r.entry_ts)
        i = pos.get(ets)
        if i is None or i < 2:
            miss += 1
            rows.append((np.nan, np.nan, np.nan))
            continue
        dir_sign = 1.0 if r.side == "long" else -1.0
        cd3 = dir_sign * float(delta[i - 2: i + 1].sum())
        v3 = float(vol[i - 2: i + 1].sum())
        cd3_ratio = cd3 / v3 if v3 > 0 else np.nan
        # RVOL: breakout bar volume vs prior-20-session same-TOD mean
        g = tod_groups.get(bars5["tod"].iloc[i])
        rvol = np.nan
        if g is not None:
            prior = g.loc[g.index < ets, "volume"]
            if len(prior) >= 20:
                rvol = float(vol[i]) / float(prior.iloc[-20:].mean())
        rows.append((cd3_ratio, rvol, float(vol[i])))
    out = trades.copy()
    out[["cd3_ratio", "rvol", "bo_vol"]] = pd.DataFrame(rows, index=trades.index)
    if miss:
        print(f"  [warn] {miss} trades had no matching 5min breakout bar (skipped in proxy cols)")
    return out


def bucket_report(df: pd.DataFrame, col: str, label: str) -> dict:
    d = df.dropna(subset=[col])
    n = len(d)
    if n < 20:
        print(f"\n{label}: insufficient data (n={n})")
        return {}
    lo_thr, hi_thr = d[col].quantile([0.40, 0.60])
    bot = d[d[col] <= lo_thr]
    top = d[d[col] >= hi_thr]
    pf_bot, pf_top = pf(bot["realized_pnl"]), pf(top["realized_pnl"])
    ratio = pf_top / pf_bot if pf_bot > 0 else float("inf")
    print(f"\n=== {label} (n={n}) ===")
    print(f"  bottom-40% {col} (<= {lo_thr:.4g}): n={len(bot)} WR={ (bot['realized_pnl']>0).mean():.3f} PF={pf_bot:.3f} net=${bot['realized_pnl'].sum():,.0f}")
    print(f"  top-40%    {col} (>= {hi_thr:.4g}): n={len(top)} WR={ (top['realized_pnl']>0).mean():.3f} PF={pf_top:.3f} net=${top['realized_pnl'].sum():,.0f}")
    print(f"  top/bottom PF ratio = {ratio:.3f}  (GO threshold 1.40x)")
    # per-year consistency
    print(f"  per-year top vs bottom PF:")
    for yr in sorted(d["year"].unique()):
        dy = d[d["year"] == yr]
        by = dy[dy[col] <= dy[col].quantile(0.40)]
        ty = dy[dy[col] >= dy[col].quantile(0.60)]
        print(f"    {yr}: top PF={pf(ty['realized_pnl']):.3f} (n={len(ty)})  bottom PF={pf(by['realized_pnl']):.3f} (n={len(by)})")
    return {"ratio": ratio, "pf_top": pf_top, "pf_bot": pf_bot, "n": n}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default=str(REPO / "research/mfe_mae_orb_clean.csv"))
    ap.add_argument("--bars", default=str(REPO / "bars/bars_MNQ_dbv_2021_2026.csv"))
    args = ap.parse_args()

    trades = pd.read_csv(args.trades, parse_dates=["entry_ts", "exit_ts"])
    print(f"Loaded {len(trades)} ORB trades; baseline WR={ (trades['realized_pnl']>0).mean():.3f} "
          f"PF={pf(trades['realized_pnl']):.3f} net=${trades['realized_pnl'].sum():,.0f}")
    print("Resampling bars to 5min (label=close minute)...")
    bars5 = load_bars_5min(Path(args.bars))
    df = enrich(trades, bars5)

    # 1) directional cum-delta proxy
    r_cd = bucket_report(df, "cd3_ratio", "Cum-delta directional proxy (cd3_ratio)")
    # confirmed vs not-confirmed (sign split)
    dd = df.dropna(subset=["cd3_ratio"])
    conf = dd[dd["cd3_ratio"] > 0]
    notc = dd[dd["cd3_ratio"] <= 0]
    print(f"\n  CONFIRMED (cd3_ratio>0): n={len(conf)} ({len(conf)/len(dd):.0%}) WR={(conf['realized_pnl']>0).mean():.3f} PF={pf(conf['realized_pnl']):.3f} net=${conf['realized_pnl'].sum():,.0f}")
    print(f"  NOT-CONF (cd3_ratio<=0): n={len(notc)} ({len(notc)/len(dd):.0%}) WR={(notc['realized_pnl']>0).mean():.3f} PF={pf(notc['realized_pnl']):.3f} net=${notc['realized_pnl'].sum():,.0f}")

    # 2) RVOL
    r_rv = bucket_report(df, "rvol", "RVOL (breakout bar volume vs prior-20 same-TOD)")

    # 3) combined: confirmed AND high RVOL
    cc = df.dropna(subset=["cd3_ratio", "rvol"])
    rv_med = cc["rvol"].median()
    combo = cc[(cc["cd3_ratio"] > 0) & (cc["rvol"] >= rv_med)]
    rest = cc.drop(combo.index)
    print(f"\n=== Combined CONFIRM gate (cd3_ratio>0 AND rvol>=median {rv_med:.2f}) ===")
    print(f"  gate-pass: n={len(combo)} ({len(combo)/len(cc):.0%}) WR={(combo['realized_pnl']>0).mean():.3f} PF={pf(combo['realized_pnl']):.3f} net=${combo['realized_pnl'].sum():,.0f}")
    print(f"  gate-fail: n={len(rest)} ({len(rest)/len(cc):.0%}) WR={(rest['realized_pnl']>0).mean():.3f} PF={pf(rest['realized_pnl']):.3f} net=${rest['realized_pnl'].sum():,.0f}")
    cratio = pf(combo['realized_pnl'])/pf(rest['realized_pnl']) if pf(rest['realized_pnl'])>0 else float('inf')
    print(f"  gate-pass/gate-fail PF ratio = {cratio:.3f}")

    print("\n--- VERDICT ---")
    cd_ratio = r_cd.get("ratio", 0)
    print(f"cd3_ratio top/bottom PF ratio: {cd_ratio:.3f} ({'GO' if cd_ratio>=1.4 else 'NO-GO'} vs 1.40x)")
    if r_rv:
        print(f"rvol top/bottom PF ratio: {r_rv['ratio']:.3f} ({'GO' if r_rv['ratio']>=1.4 else 'NO-GO'} vs 1.40x)")


if __name__ == "__main__":
    main()
