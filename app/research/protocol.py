"""Pre-registered research protocols (spec §2).

Lifecycle: a human edits research/protocols/<id>.yaml as a draft, then
`python -m app.research protocol lock <id>` hashes the canonical form, stores
it in the ledger and makes the file read-only. Every backtest run with
`--protocol <id>` calls verify_protocol() first and refuses on a mismatch.

The hash covers the parsed YAML (json, sorted keys), so comments and
whitespace are free to change; any value change is a new protocol version.
After lock, the ledger's holdout_status is authoritative — the YAML value
is only the starting status.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from app.backtest.costs import CostSpec
from app.sim.paper import TICK_SIZE, TICK_VALUE
from app.research.ledger import Ledger

log = logging.getLogger(__name__)

PROTOCOLS_DIR = Path("research/protocols")
HOLDOUT_STATUSES = ("sealed", "partially_used", "burned")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]*$")
_REQUIRED = ("protocol_id", "created", "owner", "universe", "bar_data", "periods",
             "session", "costs", "cost_stress_multiplier", "requirements",
             "pass_criteria_dev", "pass_criteria_holdout", "budgets", "prop_sim")


class ProtocolError(Exception):
    pass


class ProtocolNotLocked(ProtocolError):
    pass


class ProtocolHashMismatch(ProtocolError):
    pass


@dataclass(frozen=True)
class LockedProtocol:
    id: str
    hash: str
    data: dict
    holdout_status: str


def protocol_path(protocol_id: str, protocols_dir: Path = PROTOCOLS_DIR) -> Path:
    if not _ID_RE.match(protocol_id):
        raise ProtocolError(f"invalid protocol id {protocol_id!r}")
    return protocols_dir / f"{protocol_id}.yaml"


def canonical_hash(data: dict) -> str:
    canon = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode()).hexdigest()


def validate(data: dict, protocol_id: str) -> None:
    """Structural checks worth failing a lock over. Values are the human's call."""
    missing = [k for k in _REQUIRED if k not in data]
    if missing:
        raise ProtocolError(f"{protocol_id}: missing keys {missing}")
    if data["protocol_id"] != protocol_id:
        raise ProtocolError(f"{protocol_id}: protocol_id field is {data['protocol_id']!r}")
    status = data["periods"].get("holdout_status")
    if status not in HOLDOUT_STATUSES:
        raise ProtocolError(f"{protocol_id}: holdout_status {status!r} not in {HOLDOUT_STATUSES}")
    # The one-shot rule is also a unique index in the ledger; a protocol
    # claiming more than one eval per strategy would be silently capped.
    if data["budgets"].get("holdout_evals_per_strategy") != 1:
        raise ProtocolError(f"{protocol_id}: budgets.holdout_evals_per_strategy must be 1")
    if int(data["budgets"].get("holdout_evals_before_burned", 0)) < 1:
        raise ProtocolError(f"{protocol_id}: budgets.holdout_evals_before_burned must be >= 1")
    uncosted = [i for i in data["universe"] if i not in data["costs"]]
    if uncosted:
        raise ProtocolError(f"{protocol_id}: universe instruments without costs {uncosted}")
    # A protocol whose contract math disagrees with the broker would make net R
    # stop reconciling with simulated P&L. Commission and slippage may differ.
    for inst, c in data["costs"].items():
        if inst not in TICK_SIZE or inst not in TICK_VALUE:
            raise ProtocolError(f"{protocol_id}: no contract specs for {inst} in app/sim/paper.py")
        if Decimal(str(c["tick"])) != TICK_SIZE[inst] or Decimal(str(c["tick_value"])) != TICK_VALUE[inst]:
            raise ProtocolError(
                f"{protocol_id}: {inst} tick/tick_value {c['tick']}/{c['tick_value']} "
                f"!= broker {TICK_SIZE[inst]}/{TICK_VALUE[inst]}")


def load_draft(protocol_id: str, protocols_dir: Path = PROTOCOLS_DIR) -> tuple[str, dict]:
    path = protocol_path(protocol_id, protocols_dir)
    if not path.exists():
        raise ProtocolError(f"no protocol file {path}")
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ProtocolError(f"{path}: not a YAML mapping")
    return text, data


def lock_protocol(protocol_id: str, ledger: Ledger, locked_by: str,
                  protocols_dir: Path = PROTOCOLS_DIR) -> LockedProtocol:
    """Lock a draft. Re-locking an unchanged file is a no-op; a changed file
    under an already-locked id is refused (bump the version instead)."""
    text, data = load_draft(protocol_id, protocols_dir)
    validate(data, protocol_id)
    h = canonical_hash(data)
    row = ledger.get_protocol(protocol_id)
    if row is not None:
        if row["hash"] != h:
            raise ProtocolHashMismatch(
                f"{protocol_id} is already locked with hash {row['hash'][:12]}; "
                f"file now hashes to {h[:12]}. Create a new protocol version.")
        log.info("protocol %s already locked (%s)", protocol_id, h[:12])
    else:
        ledger.insert_protocol(protocol_id, h, text, locked_by,
                               data["periods"]["holdout_status"])
        log.info("protocol %s locked by %s: %s", protocol_id, locked_by, h)
    path = protocol_path(protocol_id, protocols_dir)
    os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    row = ledger.get_protocol(protocol_id)
    return LockedProtocol(protocol_id, h, data, row["holdout_status"])


def verify_protocol(protocol_id: str, ledger: Ledger,
                    protocols_dir: Path = PROTOCOLS_DIR) -> LockedProtocol:
    """Gate every protocol run. Raises unless the file is locked and unchanged."""
    row = ledger.get_protocol(protocol_id)
    if row is None:
        raise ProtocolNotLocked(f"protocol {protocol_id} is not locked in {ledger.path}")
    _, data = load_draft(protocol_id, protocols_dir)
    h = canonical_hash(data)
    if h != row["hash"]:
        log.error("protocol %s hash mismatch: ledger %s, file %s — refusing to run",
                  protocol_id, row["hash"], h)
        raise ProtocolHashMismatch(
            f"protocol {protocol_id} changed since lock (ledger {row['hash'][:12]}, "
            f"file {h[:12]}); restore it or lock a new version")
    return LockedProtocol(protocol_id, h, data, row["holdout_status"])


def protocol_cost_spec(protocol: LockedProtocol, instrument: str) -> CostSpec:
    costs = protocol.data["costs"]
    if instrument not in costs:
        raise ProtocolError(f"{instrument} has no costs in protocol {protocol.id}")
    c = costs[instrument]
    slip = c["slip_ticks"]
    return CostSpec(tick=Decimal(str(c["tick"])), tick_value=Decimal(str(c["tick_value"])),
                    commission_rt=Decimal(str(c["commission"])),
                    slip_ticks_market=int(slip["market"]), slip_ticks_stop=int(slip["stop"]),
                    slip_ticks_limit=int(slip["limit"]))
