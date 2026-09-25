"""Anomaly-table and spend-log locations.

Both are derived/runtime state, not specs — same reasoning as
research/data/paths.py and research/ledger/paths.py: they live under
`var/`, gitignored, never under `research/` or `docs/`.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

ANOMALY_ROOT = REPO_ROOT / "var" / "anomaly"
ANOMALY_TABLE_PATH = ANOMALY_ROOT / "anomaly_table.parquet"
GROK_SPEND_PATH = ANOMALY_ROOT / "grok_spend.json"
# Append-only, flushed per label: the durable record of every label already
# paid for. The parquet table is rebuilt from it, so an interrupted run
# loses nothing (research/anomaly/label_store.py).
LABELS_JSONL_PATH = ANOMALY_ROOT / "labels.jsonl"
