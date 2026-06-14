"""
B64 Phase 1: SMT Divergence (NQ vs ES) iFVG reversal filter.

Hypothesis: at the iFVG sweep bar, NQ sweeps a prior swing level but ES does NOT
-> "SMT divergence present" -> higher quality reversal signal.

Operational definition per bar:
- LONG: sweep_bar = argmin(NQ.low) in [entry_ts-20 bars, entry_ts-1 bar].
  SMT_div = ES.low[sweep_bar] >= min(ES.low[sweep_bar-5..sweep_bar-1])
  (ES did NOT confirm NQ's new low)
- SHORT: sweep_bar = argmax(NQ.high) in [entry_ts-20 bars, entry_ts-1 bar].
  SMT_div = ES.high[sweep_bar] <= max(ES.high[sweep_bar-5..sweep_bar-1])
  (ES did NOT confirm NQ's new high)

GO criterion: divergence_PF / no_divergence_PF >= 1.25 per side.
Excludes 2022 (frozen holdout). Excludes trades without enough lookback.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

REPO = Path(__file__).resolve().parents[1]
NQ_BARS = REPO / "bars" / "bars_MNQ_dbv_2021_2026.csv"
ES_BARS = REPO / "bars" / "bars_ES_dbv_2021_2026.csv"
TRADES = REPO / "research" / "mfe_mae_ifvg_clean.csv"

LOOKBACK_BARS = 20   # bars before entry to search for sweep
ES_CONFIRM_WINDOW = 5  # bars before sweep_bar to define ES's prior range
MIN_BAR_IDX = 30     # skip trades without enough history


def bucket_floor(ts: pd.Timestamp, minutes: int = 5) -> pd.Timestamp:
    total_min = ts.hour * 60 + ts.minute
    floored = (total_min // minutes) * minutes
    return ts.replace(hour=floored // 60, minute=floored % 60, second=0, microsecond=0, tzinfo=ts.tzinfo)


def build_5min(path: Path) -> pd.DataFrame:
    print(f"  Loading {path.name}...", flush=True)
    df = pd.read_csv(path, parse_dates=["ts"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df["bucket"] = df["ts"].apply(lambda t: bucket_floor(t))
    bars5 = (
        df.groupby("bucket")
        .agg(open=("open", "first"), high=("high", "max"),
             low=("low", "min"), close=("close", "last"), volume=("volume", "sum"))
        .reset_index()
        .rename(columns={"bucket": "ts"})
        .sort_values("ts")
        .reset_index(drop=True)
    )
    return bars5


def pf(pnl_series: pd.Series) -> float:
    wins = pnl_series[pnl_series > 0].sum()
    losses = pnl_series[pnl_series < 0].abs().sum()
    return wins / losses if losses > 0 else float("nan")


def main():
    print("B64 Phase 1: SMT Divergence analysis", flush=True)

    # Build 5min bars
    nq5 = build_5min(NQ_BARS)
    es5 = build_5min(ES_BARS)

    # Build timestamp index for fast lookups
    nq_ts_idx = {ts: i for i, ts in enumerate(nq5["ts"])}
    es_ts_idx = {ts: i for i, ts in enumerate(es5["ts"])}
    nq_low = nq5["low"].values
    nq_high = nq5["high"].values
    es_low = es5["low"].values
    es_high = es5["high"].values
    nq_ts_arr = nq5["ts"].values  # for debug

    print(f"  NQ 5min bars: {len(nq5):,}, ES 5min bars: {len(es5):,}", flush=True)

    # Load trades (exclude 2022)
    trades = pd.read_csv(TRADES, parse_dates=["entry_ts", "exit_ts"])
    trades["entry_ts"] = pd.to_datetime(trades["entry_ts"], utc=True)
    trades = trades[trades["year"] != 2022].copy()
    print(f"  Trades (excl 2022): {len(trades)}", flush=True)

    labels = []
    skipped = 0

    for _, row in trades.iterrows():
        side = row["side"]
        entry_ts = row["entry_ts"]
        pnl = row["realized_pnl"]

        # Find NQ entry bar
        entry_bucket = bucket_floor(entry_ts)
        nq_idx = nq_ts_idx.get(entry_bucket)
        if nq_idx is None:
            skipped += 1
            continue

        # Window: last LOOKBACK_BARS bars BEFORE entry bar (not including entry bar)
        win_start = max(0, nq_idx - LOOKBACK_BARS)
        win_end = nq_idx  # exclusive: bars [win_start, nq_idx)

        if (win_end - win_start) < 5 or nq_idx < MIN_BAR_IDX:
            skipped += 1
            continue

        window_low = nq_low[win_start:win_end]
        window_high = nq_high[win_start:win_end]

        if side == "long":
            # Sweep bar = bar with lowest NQ low in window
            sweep_offset = int(np.argmin(window_low))
            sweep_bar_nq_idx = win_start + sweep_offset
        else:
            # Sweep bar = bar with highest NQ high in window
            sweep_offset = int(np.argmax(window_high))
            sweep_bar_nq_idx = win_start + sweep_offset

        # Look up ES at the same timestamp
        sweep_ts = nq5["ts"].iloc[sweep_bar_nq_idx]
        es_sweep_idx = es_ts_idx.get(sweep_ts)
        if es_sweep_idx is None:
            # ES may not trade overnight; try adjacent bars within ±2 slots
            found = False
            for delta in [1, -1, 2, -2]:
                candidate_nq_idx = sweep_bar_nq_idx + delta
                if 0 <= candidate_nq_idx < len(nq5):
                    candidate_ts = nq5["ts"].iloc[candidate_nq_idx]
                    es_sweep_idx = es_ts_idx.get(candidate_ts)
                    if es_sweep_idx is not None:
                        found = True
                        break
            if not found:
                skipped += 1
                continue

        # ES prior range: bars [es_sweep_idx - ES_CONFIRM_WINDOW, es_sweep_idx)
        es_prior_start = max(0, es_sweep_idx - ES_CONFIRM_WINDOW)
        if es_prior_start >= es_sweep_idx:
            skipped += 1
            continue

        if side == "long":
            es_prior_min = float(np.min(es_low[es_prior_start:es_sweep_idx]))
            es_sweep_low = float(es_low[es_sweep_idx])
            # SMT divergence: ES did NOT confirm NQ's new low
            smt_div = es_sweep_low >= es_prior_min
        else:
            es_prior_max = float(np.max(es_high[es_prior_start:es_sweep_idx]))
            es_sweep_high = float(es_high[es_sweep_idx])
            # SMT divergence: ES did NOT confirm NQ's new high
            smt_div = es_sweep_high <= es_prior_max

        labels.append({
            "year": row["year"],
            "side": side,
            "entry_ts": entry_ts,
            "pnl": pnl,
            "smt_divergence": smt_div,
        })

    ldf = pd.DataFrame(labels)
    print(f"\n  Labeled: {len(ldf)}, skipped: {skipped}")
    print(f"  SMT divergence rate: {ldf['smt_divergence'].mean():.1%}")
    print()

    # PF per side x divergence x year
    print("=" * 60)
    print("PF by SIDE x SMT_DIVERGENCE")
    print("=" * 60)

    go_signals = []

    for side in ["long", "short"]:
        subset = ldf[ldf["side"] == side]
        div = subset[subset["smt_divergence"]]
        nodiv = subset[~subset["smt_divergence"]]

        pf_div = pf(div["pnl"])
        pf_nodiv = pf(nodiv["pnl"])
        ratio = pf_div / pf_nodiv if pf_nodiv > 0 else float("nan")

        n_div = len(div)
        n_nodiv = len(nodiv)
        wrate_div = (div["pnl"] > 0).mean() if n_div > 0 else float("nan")
        wrate_nodiv = (nodiv["pnl"] > 0).mean() if n_nodiv > 0 else float("nan")

        go = "GO" if ratio >= 1.25 else "NO-GO"
        go_signals.append(go)

        print(f"\n  Side: {side.upper()}")
        print(f"    Divergent   (n={n_div:4d}): PF={pf_div:.3f} WR={wrate_div:.1%}")
        print(f"    NonDivergent(n={n_nodiv:4d}): PF={pf_nodiv:.3f} WR={wrate_nodiv:.1%}")
        print(f"    Ratio: {ratio:.3f}x  -->  {go} (threshold 1.25x)")

    # Year consistency check for longs
    print("\n" + "=" * 60)
    print("Year consistency (LONGS, SMT_div PF vs no-div PF):")
    print("=" * 60)
    longs = ldf[ldf["side"] == "long"]
    consistent_years = 0
    total_years = 0
    for yr, g in longs.groupby("year"):
        div = g[g["smt_divergence"]]
        nodiv = g[~g["smt_divergence"]]
        pf_d = pf(div["pnl"])
        pf_nd = pf(nodiv["pnl"])
        r = pf_d / pf_nd if pf_nd > 0 else float("nan")
        consistent = (not np.isnan(r)) and r >= 1.25
        if consistent:
            consistent_years += 1
        total_years += 1
        print(f"  {yr}: div PF={pf_d:.3f}(n={len(div)}) vs no-div PF={pf_nd:.3f}(n={len(nodiv)}) ratio={r:.3f} {'OK' if consistent else 'FAIL'}")

    print(f"\n  Year consistency: {consistent_years}/{total_years}")

    print("\n" + "=" * 60)
    overall_go = all(g == "GO" for g in go_signals)
    verdict = "PHASE 1 GO" if overall_go else "PHASE 1 NO-GO"
    print(f"VERDICT: {verdict}")
    if not overall_go:
        print("  SMT divergence does not materially separate iFVG outcomes.")
        print("  No engine built. Join failed external-signal list.")
    else:
        print("  Proceed to Phase 2: build smt_filter_enabled engine.")
    print("=" * 60)


if __name__ == "__main__":
    main()
