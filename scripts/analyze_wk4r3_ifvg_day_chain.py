"""
wk4-r3 Phase 1 analysis: Same-day iFVG outcome chain
Does the outcome of the first iFVG signal of the day predict the quality of
subsequent same-day iFVG signals?

From B63: combined (iFVG+ORB) two-trade days showed PF_after_win=1.394 vs
PF_after_loss=0.946. This analysis checks the iFVG-only chain.

Also: day-of-week PF analysis.
"""

import pandas as pd
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IFVG_CSV = ROOT / "research" / "mfe_mae_ifvg_clean.csv"
ORB_CSV = ROOT / "research" / "mfe_mae_orb_clean.csv"


_ET = "America/New_York"


def load_ifvg() -> pd.DataFrame:
    df = pd.read_csv(IFVG_CSV)
    df["entry_ts"] = pd.to_datetime(df["entry_ts"], utc=True).dt.tz_convert(_ET)
    df["exit_ts"] = pd.to_datetime(df["exit_ts"], utc=True).dt.tz_convert(_ET)
    # exclude 2022 holdout
    df = df[df["entry_ts"].dt.year != 2022].copy()
    df["date"] = df["entry_ts"].dt.date
    df["dow"] = df["entry_ts"].dt.day_name()
    df["hour_et"] = df["entry_ts"].dt.hour
    df["win"] = df["realized_pnl"] > 0
    return df


def pf(group: pd.Series) -> float:
    wins = group[group > 0].sum()
    losses = -group[group < 0].sum()
    if losses == 0:
        return float("inf") if wins > 0 else float("nan")
    return wins / losses


def analyze_day_chain(df: pd.DataFrame) -> None:
    """
    For days with 2+ iFVG signals, classify each non-first signal by whether
    the FIRST signal of that day was a win or loss, then compute PF.
    Also split: 2nd signal after 1st WIN vs after 1st LOSS.
    """
    print("\n=== SAME-DAY iFVG OUTCOME CHAIN ANALYSIS ===")
    print(f"Total iFVG trades (excl 2022): {len(df)}")

    # Sort by entry_ts within each date
    df_sorted = df.sort_values(["date", "entry_ts"])

    results = []
    for date, grp in df_sorted.groupby("date"):
        grp = grp.reset_index(drop=True)
        if len(grp) < 2:
            continue  # only single signal that day, skip
        first_outcome = grp.loc[0, "win"]
        for i in range(1, len(grp)):
            results.append({
                "date": date,
                "signal_rank": i + 1,  # 2nd, 3rd, etc.
                "first_win": first_outcome,
                "pnl": grp.loc[i, "realized_pnl"],
                "win": grp.loc[i, "win"],
            })

    if not results:
        print("No multi-iFVG days found.")
        return

    rdf = pd.DataFrame(results)
    n_multi = df_sorted.groupby("date").size()
    n_multi_days = (n_multi >= 2).sum()
    print(f"Days with 2+ iFVG signals: {n_multi_days}")
    print(f"Non-first signals on multi-iFVG days: {len(rdf)}")

    # Overall PF of non-first signals
    print(f"\nOverall PF (non-first signals): {pf(rdf['pnl']):.3f}  n={len(rdf)}")

    # After first WIN
    aw = rdf[rdf["first_win"] == True]["pnl"]
    print(f"After first iFVG WIN:  PF={pf(aw):.3f}  n={len(aw)}  WR={aw.gt(0).mean():.1%}")

    # After first LOSS
    al = rdf[rdf["first_win"] == False]["pnl"]
    print(f"After first iFVG LOSS: PF={pf(al):.3f}  n={len(al)}  WR={al.gt(0).mean():.1%}")

    ratio = pf(aw) / pf(al) if pf(al) > 0 else float("inf")
    print(f"After-win/After-loss PF ratio: {ratio:.3f}  (GO threshold: >= 1.30)")

    # Per-year breakdown
    print("\nPer-year breakdown (after-win vs after-loss PF):")
    rdf["year"] = pd.to_datetime(rdf["date"]).dt.year
    for yr in sorted(rdf["year"].unique()):
        yw = rdf[(rdf["year"] == yr) & (rdf["first_win"] == True)]["pnl"]
        yl = rdf[(rdf["year"] == yr) & (rdf["first_win"] == False)]["pnl"]
        pfw = pf(yw)
        pfl = pf(yl)
        r = pfw / pfl if pfl > 0 else float("inf")
        print(f"  {yr}: after-win PF={pfw:.3f} (n={len(yw)}) | after-loss PF={pfl:.3f} (n={len(yl)}) | ratio={r:.3f}")

    # GO/NO-GO verdict
    print(f"\nPhase 1 verdict: {'GO' if ratio >= 1.30 else 'NO-GO'}")

    # Also break down by signal rank (2nd vs 3rd vs 4th+)
    print("\nBy signal rank:")
    for rank in sorted(rdf["signal_rank"].unique()):
        sub = rdf[rdf["signal_rank"] == rank]["pnl"]
        print(f"  Rank {rank}: PF={pf(sub):.3f}  n={len(sub)}")

    # Add: what fraction of trades on multi-signal days come after a loss?
    print(f"\nFraction of non-first signals coming after a loss: {(~rdf['first_win']).mean():.1%}")
    print(f"  -> A 'skip after loss' filter would remove {(~rdf['first_win']).sum()} trades ({(~rdf['first_win']).mean():.1%} of non-first signals)")


def analyze_dow(df: pd.DataFrame) -> None:
    """Day-of-week PF analysis."""
    print("\n=== DAY-OF-WEEK iFVG PF ANALYSIS ===")
    dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    print(f"{'DOW':<12} {'n':>6} {'WR%':>6} {'PF':>6} {'Net$':>10}")
    for dow in dow_order:
        sub = df[df["dow"] == dow]["realized_pnl"]
        if len(sub) == 0:
            continue
        print(f"{dow:<12} {len(sub):>6} {sub.gt(0).mean():>6.1%} {pf(sub):>6.3f} {sub.sum():>10.0f}")

    print("\nPer-year DOW breakdown (PF):")
    for yr in sorted(df["entry_ts"].dt.year.unique()):
        ydf = df[df["entry_ts"].dt.year == yr]
        row = []
        for dow in dow_order:
            sub = ydf[ydf["dow"] == dow]["realized_pnl"]
            row.append(f"{dow[:3]}:{pf(sub):.2f}({len(sub)})")
        print(f"  {yr}: " + " | ".join(row))


def analyze_session_hour(df: pd.DataFrame) -> None:
    """Per-hour iFVG PF (to cross-check against prior research)."""
    print("\n=== PER-HOUR iFVG PF (deployed long-only excl 2022) ===")
    df_long = df[df["side"] == "long"] if "side" in df.columns else df
    for hour in range(0, 24):
        sub = df_long[df_long["hour_et"] == hour]["realized_pnl"]
        if len(sub) >= 10:
            print(f"  {hour:02d}:xx  PF={pf(sub):.3f}  n={len(sub)}")


def analyze_combined_chain(ifvg_df: pd.DataFrame, orb_df: pd.DataFrame) -> None:
    """Verify B63 finding: second trade of day (any engine) after first win vs first loss."""
    print("\n=== B63 VERIFICATION: second trade after first win/loss (combined iFVG+ORB) ===")
    orb_df["entry_ts"] = pd.to_datetime(orb_df["entry_ts"], utc=True).dt.tz_convert(_ET)
    orb_df["exit_ts"] = pd.to_datetime(orb_df["exit_ts"], utc=True).dt.tz_convert(_ET)
    orb_df = orb_df[orb_df["entry_ts"].dt.year != 2022].copy()
    orb_df["date"] = orb_df["entry_ts"].dt.date

    combined = pd.concat([
        ifvg_df[["date", "entry_ts", "realized_pnl", "win"]],
        orb_df[["date", "entry_ts", "realized_pnl"]].assign(win=orb_df["realized_pnl"] > 0),
    ]).sort_values(["date", "entry_ts"]).reset_index(drop=True)

    results = []
    for date, grp in combined.groupby("date"):
        grp = grp.reset_index(drop=True)
        if len(grp) < 2:
            continue
        first_win = grp.loc[0, "win"]
        for i in range(1, len(grp)):
            results.append({
                "date": date,
                "first_win": first_win,
                "pnl": grp.loc[i, "realized_pnl"],
            })

    if not results:
        return
    rdf = pd.DataFrame(results)
    aw = rdf[rdf["first_win"] == True]["pnl"]
    al = rdf[rdf["first_win"] == False]["pnl"]
    print(f"Two-trade+ days (combined): {len(rdf)} second-signals")
    print(f"After first WIN:  PF={pf(aw):.3f}  n={len(aw)}")
    print(f"After first LOSS: PF={pf(al):.3f}  n={len(al)}")
    print(f"Ratio: {pf(aw)/pf(al):.3f}  (B63 reference: 1.473x)")


def main() -> None:
    print("Loading iFVG MFE/MAE data...")
    df = load_ifvg()
    print(f"Loaded {len(df)} iFVG trades (excl 2022)")
    print(f"Years: {sorted(df['entry_ts'].dt.year.unique())}")

    analyze_day_chain(df)
    analyze_dow(df)

    if ORB_CSV.exists():
        orb_df = pd.read_csv(ORB_CSV, parse_dates=["entry_ts", "exit_ts"])
        analyze_combined_chain(df, orb_df)

    print("\nDone.")


if __name__ == "__main__":
    main()
