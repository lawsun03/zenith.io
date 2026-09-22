#!/usr/bin/env python3
"""Seal the holdout directory: chmod it unreadable by anyone.

This is the explicit human action CLAUDE.md domain invariant 6 requires
("It is opened only by explicit human action, and every touch is logged").
Nothing under research/ calls this, and nothing under research/ ever
unseals it — that would defeat the point.

Usage:
    python scripts/seal_holdout.py            # seal all three instruments
    python scripts/seal_holdout.py --unseal   # deliberate human review only
"""
from __future__ import annotations

import argparse
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research.data.instruments import ROOTS  # noqa: E402
from research.data.paths import holdout_dir  # noqa: E402

LOG_PATH = REPO_ROOT / "docs" / "research-loop" / "holdout_access_log.txt"


def _log(action: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} {action}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unseal", action="store_true", help="restore normal permissions (human review only)")
    args = parser.parse_args()

    for root in ROOTS:
        d = holdout_dir(root)
        if not d.exists():
            print(f"{d}: does not exist, skipping", file=sys.stderr)
            continue
        if args.unseal:
            d.chmod(stat.S_IRWXU)
            print(f"unsealed {d} — human review only, reseal when done")
            _log(f"UNSEAL {d}")
        else:
            d.chmod(0o000)
            print(f"sealed {d}")
            _log(f"SEAL {d}")


if __name__ == "__main__":
    main()
