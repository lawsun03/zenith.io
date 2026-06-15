"""B12 backfill: insert missing rows from trades_2026-06-10.csv into trades.csv."""
import sys

# Read trades.csv
with open("trades/trades.csv", "r", newline="", encoding="utf-8") as f:
    content = f.read()
lines = content.splitlines(keepends=True)

# Read the daily backup (skip header)
with open("trades/trades_2026-06-10.csv", "r", newline="", encoding="utf-8") as f:
    daily_lines = f.read().splitlines(keepends=True)
daily_data_lines = daily_lines[1:]  # skip header

# Find first line starting with 2026-06-12 (insertion point)
insert_before = None
for i, line in enumerate(lines):
    if i == 0:
        continue
    if line.startswith("2026-06-12"):
        insert_before = i
        break

if insert_before is None:
    print("ERROR: 2026-06-12 entry not found in trades.csv")
    sys.exit(1)

print(f"Insertion point: before line {insert_before + 1} (1-based)")
print(f"Daily rows available: {len(daily_data_lines)}")

# Collect existing broker_order_ids to detect duplicates
existing_ids: set[str] = set()
for line in lines[1:]:
    parts = line.split(",")
    if len(parts) > 7:
        existing_ids.add(parts[7].strip())

new_rows = []
skipped = 0
for line in daily_data_lines:
    parts = line.split(",")
    if len(parts) > 7:
        oid = parts[7].strip()
        if oid in existing_ids:
            skipped += 1
            continue
    new_rows.append(line)

print(f"Already present (skipped): {skipped}")
print(f"Net new rows to insert: {len(new_rows)}")

# Merge
merged = lines[:insert_before] + new_rows + lines[insert_before:]

# Write back
with open("trades/trades.csv", "w", newline="", encoding="utf-8") as f:
    f.writelines(merged)

print(f"Done. trades.csv: {len(lines)} -> {len(merged)} lines.")
