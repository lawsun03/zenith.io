"""Build data/news_events.csv (event_type, ts_utc) for B83 news-event straddle.

Scheduled macro releases are PUBLISHED IN ADVANCE -- using their datetimes is not
lookahead (the release VALUE is never used). Dates are DST-converted from ET local
release times via zoneinfo (CPI/PPI 08:30 ET, FOMC statement 14:00 ET).

Provenance (see trade_analysis/2026-06-14_B83_news_straddle.md):
- FOMC: federalreserve.gov FOMC calendar (exact, high confidence).
- CPI 2023: cryptopotato 2023 CPI list (confirmed full).
- CPI 2024/2025/2026: BLS pattern + confirmed anchors (usinflationcalculator,
  bls.gov archives surfaced via search).
- PPI 2025: bls.gov detailed-report schedule (search). Other PPI years: BLS
  regular pattern (PPI released within ~1 trading day of CPI). PPI dates are the
  lowest-confidence series -> the Phase-1 vol-expansion diagnostic validates every
  event against the bars and reports a date-error-robust expectancy subset.

2025 Q4 government shutdown disrupted BLS releases (Oct 2025 PPI not published;
several CPI/PPI delayed). Affected/uncertain dates are included best-effort and
left for the vol-expansion diagnostic to flag.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# Bars span 2021-06-13 -> 2026-06-11. Exclude calendar 2022 (frozen holdout) at
# the Phase-1 sim stage, not here -- the CSV is the full published calendar.

# (event_type, "YYYY-MM-DD", "HH:MM")  release time in ET local
FOMC = [  # 14:00 ET statement
    "2021-06-16", "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27",
    "2022-09-21", "2022-11-02", "2022-12-14",
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26",
    "2023-09-20", "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31",
    "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30",
    "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29",
]

CPI = [  # 08:30 ET
    # 2021 H2 (May-2021 data released 06-10, before bar start -> omit)
    "2021-07-13", "2021-08-11", "2021-09-14", "2021-10-13", "2021-11-10", "2021-12-10",
    # 2022 (holdout-filtered later)
    "2022-01-12", "2022-02-10", "2022-03-10", "2022-04-12", "2022-05-11", "2022-06-10",
    "2022-07-13", "2022-08-10", "2022-09-13", "2022-10-13", "2022-11-10", "2022-12-13",
    # 2023 (confirmed full)
    "2023-01-12", "2023-02-14", "2023-03-14", "2023-04-12", "2023-05-10", "2023-06-13",
    "2023-07-12", "2023-08-10", "2023-09-13", "2023-10-12", "2023-11-14", "2023-12-12",
    # 2024
    "2024-01-11", "2024-02-13", "2024-03-12", "2024-04-10", "2024-05-15", "2024-06-12",
    "2024-07-11", "2024-08-14", "2024-09-11", "2024-10-10", "2024-11-13", "2024-12-11",
    # 2025 (Q4 shutdown: Sep-2025 data delayed to 10-24; Oct-2025 data canceled)
    "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10", "2025-05-13", "2025-06-11",
    "2025-07-15", "2025-08-12", "2025-09-11", "2025-10-24", "2025-12-18",
    # 2026 H1 (May-2026 data released 06-10, within bars)
    "2026-01-13", "2026-02-11", "2026-03-11", "2026-04-10", "2026-05-12", "2026-06-10",
    # 2026 H2 (FORWARD schedule, added 2026-06-15 for the live CPI-day router).
    # Source: usinflationcalculator BLS mirror; 2026-07-14 cross-confirmed against
    # bls.gov archive search. Re-verify vs bls.gov/schedule/news_release/cpi.htm
    # before each go-live (BLS dates can shift, e.g. the 2025 Q4 shutdown moves).
    "2026-07-14", "2026-08-12", "2026-09-11", "2026-10-14", "2026-11-10", "2026-12-10",
]

PPI = [  # 08:30 ET (lowest-confidence series; diagnostic validates)
    "2021-07-14", "2021-08-12", "2021-09-10", "2021-10-14", "2021-11-09", "2021-12-14",
    "2022-01-13", "2022-02-15", "2022-03-15", "2022-04-13", "2022-05-12", "2022-06-14",
    "2022-07-14", "2022-08-11", "2022-09-14", "2022-10-12", "2022-11-15", "2022-12-09",
    "2023-01-18", "2023-02-16", "2023-03-15", "2023-04-13", "2023-05-11", "2023-06-14",
    "2023-07-13", "2023-08-11", "2023-09-14", "2023-10-11", "2023-11-15", "2023-12-13",
    "2024-01-12", "2024-02-16", "2024-03-14", "2024-04-11", "2024-05-14", "2024-06-13",
    "2024-07-12", "2024-08-13", "2024-09-12", "2024-10-11", "2024-11-14", "2024-12-12",
    "2025-01-14", "2025-02-13", "2025-03-13", "2025-04-11", "2025-05-15", "2025-06-12",
    "2025-07-16", "2025-08-14", "2025-09-10", "2025-11-25",
    "2026-01-15", "2026-02-27", "2026-03-13", "2026-04-14", "2026-05-15",
]


def to_utc(date_str: str, hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    y, mo, d = (int(x) for x in date_str.split("-"))
    dt_et = datetime(y, mo, d, h, m, tzinfo=ET)
    return dt_et.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def main() -> None:
    rows: list[tuple[str, str]] = []
    for d in FOMC:
        rows.append(("FOMC", to_utc(d, "14:00")))
    for d in CPI:
        rows.append(("CPI", to_utc(d, "08:30")))
    for d in PPI:
        rows.append(("PPI", to_utc(d, "08:30")))
    rows.sort(key=lambda r: r[1])

    out = Path("data/news_events.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["event_type", "ts_utc"])
        w.writerows(rows)
    print(f"wrote {len(rows)} events -> {out}")
    for et in ("CPI", "PPI", "FOMC"):
        print(f"  {et}: {sum(1 for r in rows if r[0] == et)}")


if __name__ == "__main__":
    main()
