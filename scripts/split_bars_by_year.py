"""Split bars_MNQ_dbv_2021_2026.csv into per-year files in bars/yearly/."""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "bars" / "bars_MNQ_dbv_2021_2026.csv"
OUT = Path(__file__).resolve().parent.parent / "bars" / "yearly"
OUT.mkdir(parents=True, exist_ok=True)

rows_by_year: dict[str, list[dict]] = defaultdict(list)
header: list[str] = []

with SRC.open(newline="") as f:
    reader = csv.DictReader(f)
    header = reader.fieldnames or []
    for row in reader:
        year = row["ts"][:4]
        rows_by_year[year].append(row)

for year, rows in sorted(rows_by_year.items()):
    out_path = OUT / f"bars_MNQ_dbv_{year}.csv"
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)
    print(f"{year}: {len(rows)} bars -> {out_path.name}")

print("done")
