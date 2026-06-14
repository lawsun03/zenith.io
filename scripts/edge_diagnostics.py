"""Edge-localization diagnostic ("why did it fail?") — reusable across strategies.

When a backtest is breakeven/negative in aggregate, this slices the per-trade
results by a set of DIMENSIONS (side, instrument, reward target, stop type,
hour-of-day, day-of-week, year, regime, ...) and reports where — if anywhere — a
ROBUST sub-edge hides. It is the formalization of the manual loop that turned
three "rejects" into "works" on 2026-06-14 (straddle RR, news direction, S/D
instrument). Built to be extended: a dimension is just a column on the trades
frame, so adding one = add a column (or use add_time_dims for ts-derived ones).

ANTI-OVERFIT (this is the dangerous part — slice enough and you find noise):
a bucket only counts as a HIT if it clears ALL of:
  - PF >= pf_min (default 1.2)
  - n  >= min_n  (default 30)
  - positive in >= ROBUST_FRAC of its years (default 0.6 -> 3/5, 3/3, 2/3)
  - beats control_pf if one is supplied (e.g. a random-entry baseline)
A hit is a NEW HYPOTHESIS to confirm out-of-sample, never a conclusion. If the
aggregate is already robust, sub-slices are reported but not "discoveries".

Interfaces:
  - localize(df, dims, ...) -> dict[dim] = per-bucket stats DataFrame (+ prints)
  - add_time_dims(df, ts_col) -> adds year / hour_et / dow columns
  - CLI: python scripts/edge_diagnostics.py --csv trades.csv --value R \
           --date date --dims type,side[,hour_et,dow]
"""
from __future__ import annotations
import math
import pandas as pd

ROBUST_FRAC = 0.6


def _stats(r: pd.Series, year: pd.Series) -> dict:
    n = len(r)
    if n == 0:
        return dict(n=0, win=0.0, pf=0.0, rtrd=0.0, totr=0.0, yrs_pos=0, yrs=0)
    gw = float(r[r > 0].sum()); gl = float(-r[r < 0].sum())
    pf = (gw / gl) if gl > 0 else float("inf")
    yr = r.groupby(year).sum()
    return dict(n=n, win=float((r > 0).mean() * 100), pf=pf, rtrd=float(r.mean()),
                totr=float(r.sum()), yrs_pos=int((yr > 0).sum()), yrs=int(len(yr)))


def _is_robust(s: dict, pf_min: float, min_n: int, control_pf: float | None) -> bool:
    if s["n"] < min_n or s["pf"] < pf_min or s["yrs"] == 0:
        return False
    if s["yrs_pos"] < math.ceil(ROBUST_FRAC * s["yrs"]):
        return False
    if control_pf is not None and s["pf"] <= control_pf:
        return False
    return True


def add_time_dims(df: pd.DataFrame, ts_col: str, tz: str = "America/New_York") -> pd.DataFrame:
    ts = pd.to_datetime(df[ts_col], utc=True)
    et = ts.dt.tz_convert(tz)
    df = df.copy()
    df["year"] = et.dt.year
    df["hour_et"] = et.dt.hour
    df["dow"] = et.dt.day_name().str[:3]
    return df


def localize(df: pd.DataFrame, dims: list[str], value_col: str = "r",
             year_col: str = "year", pf_min: float = 1.2, min_n: int = 30,
             control_pf: float | None = None, verbose: bool = True) -> dict:
    """Slice `df` by each dimension; report per-bucket stats + robust hits."""
    if year_col not in df.columns:
        raise ValueError(f"need a '{year_col}' column (use add_time_dims for a ts col)")
    r = pd.to_numeric(df[value_col]); yr = df[year_col]
    base = _stats(r, yr)
    base_robust = _is_robust(base, pf_min, min_n, control_pf)
    if verbose:
        ctl = f"  (control PF {control_pf})" if control_pf is not None else ""
        print(f"AGGREGATE: n={base['n']} win={base['win']:.0f}% PF={base['pf']:.2f} "
              f"R/trd={base['rtrd']:+.3f} yrs+={base['yrs_pos']}/{base['yrs']} "
              f"{'[robust]' if base_robust else '[not robust]'}{ctl}")

    results: dict[str, pd.DataFrame] = {}
    hits: list[tuple[str, object, dict]] = []
    for dim in dims:
        if dim not in df.columns:
            if verbose:
                print(f"\n[{dim}] -- column missing, skipped")
            continue
        rows = []
        for bucket, g in df.groupby(dim):
            s = _stats(pd.to_numeric(g[value_col]), g[year_col])
            robust = _is_robust(s, pf_min, min_n, control_pf)
            rows.append({dim: bucket, **s, "robust": robust})
            if robust and not base_robust:
                hits.append((dim, bucket, s))
        tbl = pd.DataFrame(rows).sort_values("pf", ascending=False)
        results[dim] = tbl
        if verbose:
            print(f"\n[{dim}]")
            for _, x in tbl.iterrows():
                flag = "  <== ROBUST HIT" if x["robust"] and not base_robust else ""
                print(f"  {str(x[dim]):<14} n={x['n']:>4} win={x['win']:>3.0f}% "
                      f"PF={x['pf']:>5.2f} R/trd={x['rtrd']:>+6.3f} "
                      f"yrs+={x['yrs_pos']}/{x['yrs']}{flag}")

    if verbose:
        print("\nVERDICT:", end=" ")
        if base_robust:
            print("aggregate already robust -- no localization needed.")
        elif hits:
            best = max(hits, key=lambda h: h[2]["pf"])
            print(f"edge LOCALIZES in {best[0]}={best[1]} "
                  f"(PF {best[2]['pf']:.2f}, {best[2]['yrs_pos']}/{best[2]['yrs']} yrs). "
                  f"{len(hits)} robust sub-edge(s) found — confirm OUT-OF-SAMPLE before trusting.")
        else:
            print("no robust sub-edge in any dimension — genuine reject (not just mis-parameterized).")
    return results


def _cli():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="per-trade CSV")
    ap.add_argument("--value", default="r", help="realized-R column")
    ap.add_argument("--date", default=None, help="ts/date column -> derives year/hour_et/dow")
    ap.add_argument("--dims", required=True, help="comma-separated dimension columns")
    ap.add_argument("--pf-min", type=float, default=1.2)
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--control-pf", type=float, default=None)
    a = ap.parse_args()
    df = pd.read_csv(a.csv)
    if a.date:
        df = add_time_dims(df, a.date)
    localize(df, a.dims.split(","), value_col=a.value, pf_min=a.pf_min,
             min_n=a.min_n, control_pf=a.control_pf)


if __name__ == "__main__":
    _cli()
