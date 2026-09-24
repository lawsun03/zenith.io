#!/usr/bin/env python3
"""Interactive conversational layer over the research ledger, backed by
Kimi K3 (docs/research-loop/PHASE-PROMPTS.md phase 7).

Opens a READ-ONLY connection (research.ledger.db.get_readonly_connection —
enforced by SQLite itself, PRAGMA query_only = ON, not by this script or
the model's good behaviour), prints loop health unprompted, then hands the
terminal to a plain chat loop. The agent's only capability is a read-only
SQL SELECT against the ledger and its canned views
(research.ledger_agent.tools); it has no write path at all.

Requires MOONSHOT_API_KEY (read from the environment, or a .env file at
the repo root).

Usage:
    python scripts/ledger_chat.py
    python scripts/ledger_chat.py --ledger-path var/ledger/research.db
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research.ledger.db import get_readonly_connection  # noqa: E402
from research.ledger.paths import LEDGER_DB_PATH  # noqa: E402
from research.ledger_agent.agent import start_session  # noqa: E402
from research.ledger_agent.kimi_client import make_kimi_tool_chat_fn  # noqa: E402
from research.ledger_agent.tools import QUERY_TOOL_SCHEMA  # noqa: E402


def _load_env_file(path: Path) -> None:
    """Minimal KEY=VALUE .env loader — matches scripts/run_anomaly_pass.py."""
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ledger-path", default=str(LEDGER_DB_PATH))
    args = parser.parse_args()

    _load_env_file(REPO_ROOT / ".env")
    api_key = os.environ.get("MOONSHOT_API_KEY")
    if not api_key:
        print("MOONSHOT_API_KEY not set (checked environment and .env)", file=sys.stderr)
        sys.exit(1)

    try:
        conn = get_readonly_connection(args.ledger_path)
    except Exception as exc:
        print(f"couldn't open {args.ledger_path} read-only: {exc}", file=sys.stderr)
        sys.exit(1)

    chat_fn = make_kimi_tool_chat_fn(api_key, tools=[QUERY_TOOL_SCHEMA])
    session = start_session(conn, chat_fn)

    print(session.opening_message())
    print("\n(read-only — ask anything about the ledger; Ctrl-D to quit)\n")

    try:
        while True:
            try:
                question = input("> ").strip()
            except EOFError:
                print()
                break
            if not question:
                continue
            print(session.ask(question))
            print()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
