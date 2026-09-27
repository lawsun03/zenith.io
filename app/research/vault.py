"""Holdout vault storage (spec §3): the ingest split and the isolation check.

Ingest splits each universe instrument's bars/bars_<SYM>.csv (from
scripts/fetch_bars_databento.py) by CME session (trading_day_ct, 5pm CT roll):

    dev      -> <dev_dir>/<protocol_id>/bars_<SYM>.csv
    holdout  -> <vault_dir>/<protocol_id>/holdout/bars_<SYM>.csv

The first `embargo_days` sessions after dev end are dropped, so rolling
features can't carry dev information into the holdout. Sessions are taken
from the union of all instruments' data, so every instrument shares one
holdout start; the effective start is the later of that and the protocol's
holdout.start. Anything outside dev and holdout is dropped.

The vault directory is enforced by the OS, not by this code: the research
worker runs where it can't list it (docs/research-protocol.md, "Vault
isolation"). assert_unreadable() is the worker-side self-check; a worker
that can list the vault must refuse to start.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.replay import _TS_KEYS, _parse_ts
from app.research.protocol import LockedProtocol
from app.risk.flatten import trading_day_ct

log = logging.getLogger(__name__)

VAULT_ENV = "ZENITH_VAULT_DIR"
DEFAULT_DEV_DIR = Path("data/dev")
MANIFEST = "manifest.json"


class VaultError(Exception):
    pass


class VaultExposed(VaultError):
    """This process can list the vault. For the research worker that means
    the isolation setup is wrong."""


def vault_dir_from_env() -> Path | None:
    v = os.environ.get(VAULT_ENV)
    return Path(v) if v else None


def holdout_dir(vault_dir: Path, protocol_id: str) -> Path:
    return vault_dir / protocol_id / "holdout"


def holdout_bars_path(vault_dir: Path, protocol_id: str, instrument: str) -> Path:
    return holdout_dir(vault_dir, protocol_id) / f"bars_{instrument}.csv"


def dev_bars_path(dev_dir: Path, protocol_id: str, instrument: str) -> Path:
    return dev_dir / protocol_id / f"bars_{instrument}.csv"


def _d(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def periods(protocol: LockedProtocol) -> tuple[date, date, date, date, int]:
    p = protocol.data["periods"]
    return (_d(p["dev"]["start"]), _d(p["dev"]["end"]), _d(p["holdout"]["start"]),
            _d(p["holdout"]["end"]), int(p["embargo_days"]))


def _rows(path: Path):
    """(session, row) per bar, with the header first as (None, header).
    Raises on out-of-order timestamps: the split and the dev check assume
    time-sorted files."""
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        lowered = [h.strip().lower() for h in header]
        ts_col = next((lowered.index(k) for k in _TS_KEYS if k in lowered), None)
        if ts_col is None:
            raise VaultError(f"{path}: no timestamp column (looked for {', '.join(_TS_KEYS)})")
        yield None, header
        prev: datetime | None = None
        for i, row in enumerate(reader, start=2):
            if not row:
                continue
            ts = _parse_ts(row[ts_col])
            if prev is not None and ts < prev:
                raise VaultError(f"{path}:{i}: timestamp {ts} before previous {prev}")
            prev = ts
            yield trading_day_ct(ts), row


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ingest(protocol: LockedProtocol, bars_dir: Path, dev_dir: Path, vault_dir: Path,
           replace: bool = False) -> dict:
    dev_start, dev_end, ho_start, ho_end, embargo_days = periods(protocol)
    sources = {i: bars_dir / f"bars_{i}.csv" for i in protocol.data["universe"]}
    missing = sorted(i for i, p in sources.items() if not p.exists())
    for i in missing:
        log.error("vault ingest: no bars for %s at %s — not split", i, sources[i])
    present = {i: p for i, p in sources.items() if i not in missing}
    if not present:
        raise VaultError(f"no universe bars under {bars_dir}")

    targets = {i: (dev_bars_path(dev_dir, protocol.id, i),
                   holdout_bars_path(vault_dir, protocol.id, i)) for i in present}
    if not replace:
        existing = [str(p) for pair in targets.values() for p in pair if p.exists()]
        if existing:
            raise VaultError(f"split already exists (pass replace to overwrite): {existing}")

    after_dev: set[date] = set()
    for path in present.values():
        after_dev.update(s for s, _ in _rows(path) if s is not None and dev_end < s <= ho_end)
    embargoed = sorted(after_dev)[:embargo_days]
    if len(embargoed) < embargo_days:
        raise VaultError(f"only {len(embargoed)} sessions after dev end {dev_end}; "
                         f"embargo needs {embargo_days}")
    holdout_first = max(ho_start, embargoed[-1] + timedelta(days=1)) if embargoed else ho_start
    log.info("vault ingest %s: dev %s..%s, embargo %s, holdout %s..%s", protocol.id,
             dev_start, dev_end, [str(d) for d in embargoed], holdout_first, ho_end)

    per_inst: dict[str, dict] = {}
    for inst, src in present.items():
        dev_path, ho_path = targets[inst]
        counts = {"dev": 0, "holdout": 0, "embargo": 0, "dropped": 0}
        spans: dict[str, list] = {"dev": [None, None], "holdout": [None, None]}
        for p in (dev_path, ho_path):
            p.parent.mkdir(parents=True, exist_ok=True)
        dev_tmp, ho_tmp = dev_path.with_suffix(".tmp"), ho_path.with_suffix(".tmp")
        with dev_tmp.open("w", newline="", encoding="utf-8") as fd, \
                ho_tmp.open("w", newline="", encoding="utf-8") as fh:
            wd, wh = csv.writer(fd), csv.writer(fh)
            for session, row in _rows(src):
                if session is None:
                    wd.writerow(row)
                    wh.writerow(row)
                    continue
                if dev_start <= session <= dev_end:
                    side, w = "dev", wd
                elif holdout_first <= session <= ho_end:
                    side, w = "holdout", wh
                else:
                    counts["embargo" if session in embargoed else "dropped"] += 1
                    continue
                w.writerow(row)
                counts[side] += 1
                spans[side][0] = spans[side][0] or session
                spans[side][1] = session
        os.replace(dev_tmp, dev_path)
        os.replace(ho_tmp, ho_path)
        per_inst[inst] = {"counts": counts,
                          "dev_sessions": [str(s) if s else None for s in spans["dev"]],
                          "holdout_sessions": [str(s) if s else None for s in spans["holdout"]],
                          "dev_sha256": _sha256(dev_path), "holdout_sha256": _sha256(ho_path)}
        log.info("vault ingest %s %s: %s", protocol.id, inst, counts)
        log.warning("vault ingest: %s still holds %s's holdout sessions; it is readable by "
                    "anything that can read %s", src, inst, bars_dir)

    common = {"protocol_id": protocol.id, "protocol_hash": protocol.hash,
              "ingested_at": datetime.now(timezone.utc).isoformat(),
              "dev": [str(dev_start), str(dev_end)],
              "embargo_sessions": [str(d) for d in embargoed],
              "holdout": [str(holdout_first), str(ho_end)], "missing": missing}
    (dev_dir / protocol.id / MANIFEST).write_text(json.dumps(
        {**common, "instruments": {i: {"rows": v["counts"]["dev"], "sessions": v["dev_sessions"],
                                       "sha256": v["dev_sha256"]} for i, v in per_inst.items()}},
        indent=2))
    holdout_dir(vault_dir, protocol.id).joinpath(MANIFEST).write_text(json.dumps(
        {**common, "instruments": {i: {"rows": v["counts"]["holdout"],
                                       "sessions": v["holdout_sessions"],
                                       "sha256": v["holdout_sha256"]} for i, v in per_inst.items()}},
        indent=2))
    return {**common, "instruments": {i: v["counts"] for i, v in per_inst.items()}}


def assert_unreadable(vault_dir: Path) -> None:
    """Worker-side check. Passes only when listing the vault is denied.
    A missing path fails too: it usually means a wrong ZENITH_VAULT_DIR,
    which would make this check pass for the wrong reason."""
    try:
        os.listdir(vault_dir)
    except PermissionError:
        return
    except FileNotFoundError:
        raise VaultError(f"vault path {vault_dir} does not exist; check {VAULT_ENV}")
    raise VaultExposed(f"this process can list the vault at {vault_dir}; the research "
                       "worker must not (docs/research-protocol.md, Vault isolation)")


def check_dev_split(protocol: LockedProtocol, dev_dir: Path) -> list[str]:
    """Problems with the worker's dev bars: any bar in a session after dev end."""
    _, dev_end, *_ = periods(protocol)
    problems = []
    for inst in protocol.data["universe"]:
        path = dev_bars_path(dev_dir, protocol.id, inst)
        if not path.exists():
            problems.append(f"{inst}: no dev bars at {path}")
            continue
        last = None
        for session, _ in _rows(path):
            last = session or last
        if last and last > dev_end:
            problems.append(f"{inst}: dev file reaches session {last}, after dev end {dev_end}")
    return problems
