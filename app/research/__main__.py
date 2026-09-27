"""`python -m app.research protocol {lock,verify} <id>` — the spec's `zenith protocol lock`."""
from __future__ import annotations

import argparse
import getpass
import logging
import sys

from app.research.ledger import DEFAULT_LEDGER_PATH, Ledger
from app.research.protocol import ProtocolError, lock_protocol, verify_protocol


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.research")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH))
    sub = parser.add_subparsers(dest="group", required=True)
    proto = sub.add_parser("protocol")
    proto.add_argument("action", choices=["lock", "verify"])
    proto.add_argument("protocol_id")
    proto.add_argument("--by", default=None, help="who is locking (default: OS user)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(name)s | %(message)s")

    ledger = Ledger(args.ledger)
    try:
        if args.action == "lock":
            p = lock_protocol(args.protocol_id, ledger, locked_by=args.by or getpass.getuser())
        else:
            p = verify_protocol(args.protocol_id, ledger)
    except ProtocolError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        ledger.close()
    print(f"{p.id} {p.hash} holdout_status={p.holdout_status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
