"""`python -m app.research ...` — the spec's `zenith` commands.

    protocol lock|verify <id> [--by]
    vault ingest <protocol_id> [--bars-dir bars] [--dev-dir data/dev] [--replace]
    vault check <protocol_id> [--dev-dir data/dev]    run as the research worker
    holdout queue
    holdout release|decline <eval_id> [--by]
    holdout evaluate <strategy_hash> <protocol_id> [--by]
    seeds register <protocol_id> [--by]

The vault path comes from --vault-dir or ZENITH_VAULT_DIR. `vault ingest`,
`holdout release` and `holdout evaluate` need to read the vault, so they only
work for its owner.
"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from pathlib import Path

from app.research.holdout import (
    HoldoutRefused, VaultService, decline, pending_requests,
)
from app.research.ledger import DEFAULT_LEDGER_PATH, Ledger
from app.research.protocol import ProtocolError, lock_protocol, verify_protocol
from app.research.seeds import register_seeds
from app.research.vault import (
    DEFAULT_DEV_DIR, VAULT_ENV, VaultError, assert_unreadable, check_dev_split, ingest,
    vault_dir_from_env,
)


def _vault_dir(args) -> Path:
    v = Path(args.vault_dir) if args.vault_dir else vault_dir_from_env()
    if v is None:
        raise VaultError(f"no vault path: pass --vault-dir or set {VAULT_ENV}")
    return v


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.research")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH))
    parser.add_argument("--vault-dir", default=None, help=f"default: ${VAULT_ENV}")
    sub = parser.add_subparsers(dest="group", required=True)

    proto = sub.add_parser("protocol")
    proto.add_argument("action", choices=["lock", "verify"])
    proto.add_argument("protocol_id")
    proto.add_argument("--by", default=None, help="who is locking (default: OS user)")

    vault = sub.add_parser("vault")
    vault.add_argument("action", choices=["ingest", "check"])
    vault.add_argument("protocol_id")
    vault.add_argument("--bars-dir", default="bars")
    vault.add_argument("--dev-dir", default=str(DEFAULT_DEV_DIR))
    vault.add_argument("--replace", action="store_true", help="overwrite an existing split")

    ho = sub.add_parser("holdout")
    ho.add_argument("action", choices=["queue", "release", "decline", "evaluate"])
    ho.add_argument("target", nargs="*", help="eval_id, or strategy_hash protocol_id")
    ho.add_argument("--by", default=None, help="approver (default: OS user)")

    seeds = sub.add_parser("seeds")
    seeds.add_argument("action", choices=["register"])
    seeds.add_argument("protocol_id")
    seeds.add_argument("--by", default=None)

    args = parser.parse_args(argv)
    if args.group == "holdout":
        want = {"queue": 0, "release": 1, "decline": 1, "evaluate": 2}[args.action]
        if len(args.target) != want:
            parser.error(f"holdout {args.action} takes {want} argument(s)")
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(name)s | %(message)s")
    by = getattr(args, "by", None) or getpass.getuser()

    ledger = Ledger(args.ledger)
    try:
        if args.group == "protocol":
            p = (lock_protocol(args.protocol_id, ledger, locked_by=by) if args.action == "lock"
                 else verify_protocol(args.protocol_id, ledger))
            print(f"{p.id} {p.hash} holdout_status={p.holdout_status}")
        elif args.group == "vault":
            p = verify_protocol(args.protocol_id, ledger)
            if args.action == "ingest":
                summary = ingest(p, Path(args.bars_dir), Path(args.dev_dir), _vault_dir(args),
                                 replace=args.replace)
                print(json.dumps(summary, indent=2))
                return 1 if summary["missing"] else 0
            assert_unreadable(_vault_dir(args))
            problems = check_dev_split(p, Path(args.dev_dir))
            for msg in problems:
                print(f"PROBLEM: {msg}", file=sys.stderr)
            if problems:
                return 1
            print(f"OK: vault not listable by {getpass.getuser()}; dev bars end by dev end")
        elif args.group == "holdout":
            if args.action == "queue":
                for r in pending_requests(ledger):
                    print(json.dumps(r))
            elif args.action == "decline":
                decline(ledger, int(args.target[0]), by)
            else:
                svc = VaultService(ledger, _vault_dir(args))
                if args.action == "release":
                    result = svc.release(int(args.target[0]), approved_by=by)
                else:
                    strategy_hash, protocol_id = args.target
                    result = svc.evaluate(strategy_hash, protocol_id, requested_by=by)
                print(json.dumps(result, indent=2, default=str))
        else:
            verify_protocol(args.protocol_id, ledger)
            for fid in register_seeds(ledger, args.protocol_id, by):
                print(f"registered {fid} (holdout_contaminated)")
    except HoldoutRefused as e:
        print(f"REFUSED (eval {e.eval_id}): {e.reason}", file=sys.stderr)
        return 1
    except (ProtocolError, VaultError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        ledger.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
