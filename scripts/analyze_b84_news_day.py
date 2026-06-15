"""B84 Phase-1a: tag iFVG/ORB trades by CPI/PPI/FOMC news day, compare PF.

Uses:
  data/news_events.csv          -- event calendar built in B83
  research/mfe_mae_deployed_combined_clean.csv  -- deployed config (close-mode, combined engine)

Reports PF / net / win-rate split by news-day vs non-news-day, per engine, per event type.
"""
import csv
from collections import defaultdict
from datetime import datetime, date
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def load_news_days(path: str) -> dict[date, set[str]]:
    """Return {et_date: {event_types}} from news_events.csv."""
    day_events: dict[date, set[str]] = defaultdict(set)
    with open(path) as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row["ts_utc"].replace("Z", "+00:00"))
            et_date = ts.astimezone(ET).date()
            day_events[et_date].add(row["event_type"])
    return day_events


def pf(trades: list[float]) -> float | None:
    wins = sum(p for p in trades if p > 0)
    losses = abs(sum(p for p in trades if p < 0))
    if losses == 0:
        return float("inf")
    return wins / losses


def report(label: str, trades: list[float]) -> None:
    n = len(trades)
    if n == 0:
        print(f"  {label}: n=0")
        return
    wins = sum(1 for p in trades if p > 0)
    net = sum(trades)
    pf_val = pf(trades)
    wr = wins / n
    print(f"  {label}: n={n}, WR={wr:.1%}, PF={pf_val:.3f}, net=${net:,.0f}")


def main() -> None:
    news_days = load_news_days("data/news_events.csv")
    print(f"News calendar: {sum(len(v) for v in news_days.values())} events across {len(news_days)} unique ET dates")

    # -- Deployed combined CSV (iFVG + ORB, close mode) --
    csv_path = "research/mfe_mae_deployed_combined_clean.csv"
    print(f"\n=== Deployed config (close-mode combined): {csv_path} ===")

    # Group trades by engine + news_day
    buckets: dict[str, dict[str, list[float]]] = {}
    # buckets[engine][group] = [pnl...]
    # group: "news", "non_news", "CPI", "PPI", "FOMC"

    year_buckets: dict[str, dict[str, dict[str, list[float]]]] = {}
    # year_buckets[engine][year][group] = [pnl...]

    with open(csv_path) as f:
        for row in csv.DictReader(f):
            engine = row["engine_type"]
            entry_ts = datetime.fromisoformat(row["entry_ts"].replace("Z", "+00:00"))
            # Skip 2022 (holdout)
            if entry_ts.year == 2022:
                continue
            et_date = entry_ts.astimezone(ET).date()
            year = str(entry_ts.year)
            pnl = float(row["pnl_usd"])
            event_types = news_days.get(et_date, set())

            if engine not in buckets:
                buckets[engine] = defaultdict(list)
                year_buckets[engine] = defaultdict(lambda: defaultdict(list))

            if event_types:
                buckets[engine]["news"].append(pnl)
                for etype in event_types:
                    buckets[engine][etype].append(pnl)
                year_buckets[engine][year]["news"].append(pnl)
            else:
                buckets[engine]["non_news"].append(pnl)
                year_buckets[engine][year]["non_news"].append(pnl)

    for engine in sorted(buckets):
        b = buckets[engine]
        print(f"\n{engine.upper()} engine:")
        report("ALL trades (excl 2022)", b["news"] + b["non_news"])
        report("Non-news days", b["non_news"])
        report("News days (CPI+PPI+FOMC)", b["news"])
        report("  CPI", b.get("CPI", []))
        report("  PPI", b.get("PPI", []))
        report("  FOMC", b.get("FOMC", []))

        # PF ratio
        pf_news = pf(b["news"])
        pf_non = pf(b["non_news"])
        if pf_news and pf_non and pf_non > 0:
            ratio = pf_non / pf_news if pf_news > 0 else float("inf")
            print(f"  non_news/news PF ratio: {ratio:.3f} (GO >= 1.3x)")

        print(f"  Year breakdown (news vs non_news PF):")
        for year in sorted(year_buckets[engine]):
            yb = year_buckets[engine][year]
            pf_n = pf(yb["news"])
            pf_nn = pf(yb["non_news"])
            flag = ""
            if pf_n is not None and pf_nn is not None:
                if pf_nn / pf_n >= 1.3 and pf_n < pf_nn:
                    flag = " <-- GO year"
                elif pf_n >= pf_nn:
                    flag = " <-- INVERTED"
            nn_str = f"{pf_nn:.3f}" if pf_nn is not None else "n/a"
            n_str = f"{pf_n:.3f}" if pf_n is not None else "n/a"
            print(f"    {year}: non_news PF={nn_str} n={len(yb['non_news'])} | news PF={n_str} n={len(yb['news'])}{flag}")

    # Summary GO/NO-GO
    print("\n=== GO/NO-GO decision ===")
    print("Threshold: overall non_news/news PF ratio >= 1.3x AND news-day PF < non_news PF in >= 4/5 years.")
    print("If GO: news-day suppression is a candidate; run equity_export + funded_sim benchmark.")
    print("If NO-GO: news days are not materially different; no suppression recommended.")


if __name__ == "__main__":
    main()
