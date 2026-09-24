"""Ledger file locations.

The DDL lives under `docs/research-loop/` because it's a spec (README: "Specs:
IR schema, gates, ledger DDL, ..."). The database it produces is runtime
state, not a spec, so it lives under `var/` alongside the Databento cache
(research/data/paths.py) and is gitignored the same way.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

LEDGER_SQL_PATH = REPO_ROOT / "docs" / "research-loop" / "ledger.sql"
LEDGER_DB_PATH = REPO_ROOT / "var" / "ledger" / "research.db"
