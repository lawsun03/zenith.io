"""Protocol lock + ledger (spec §2, §10; build step 3).

WHY: the SI/GC study moved its own goalposts — thresholds, periods and costs
drifted while candidates were being picked. A locked protocol makes the rules
fixed before results exist. These tests pin the step 3 acceptance:
(1) a run refuses when the protocol file no longer matches its locked hash,
(2) agents get a PermissionError writing or locking a protocol,
plus the ledger's append-only guarantees (§4: no deleting trials).

Not caught here: the OS read-only bit is set but not relied on (tests run as
root on CI, which ignores it) — the hash check is the real gate.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import stat
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from app.research.agent_api import AgentAPI
from app.research.ledger import Ledger
from app.research.protocol import (
    PROTOCOLS_DIR, ProtocolError, ProtocolHashMismatch, ProtocolNotLocked, canonical_hash,
    lock_protocol, protocol_cost_spec, validate, verify_protocol,
)

PID = "metals-intraday-v1"
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def pdir(tmp_path):
    d = tmp_path / "research" / "protocols"
    d.mkdir(parents=True)
    shutil.copy(REPO / PROTOCOLS_DIR / f"{PID}.yaml", d / f"{PID}.yaml")
    return d


@pytest.fixture
def ledger(tmp_path):
    lg = Ledger(tmp_path / "ledger.db")
    yield lg
    lg.close()


def _edit(path: Path, old: str, new: str) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)  # a human bypassing the read-only bit
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def test_checked_in_draft_is_valid():
    data = yaml.safe_load((REPO / PROTOCOLS_DIR / f"{PID}.yaml").read_text())
    validate(data, PID)


def test_lock_records_hash_and_makes_file_read_only(pdir, ledger):
    p = lock_protocol(PID, ledger, "lawrence", pdir)
    row = ledger.get_protocol(PID)
    assert row["hash"] == p.hash == canonical_hash(yaml.safe_load((pdir / f"{PID}.yaml").read_text()))
    assert row["holdout_status"] == "partially_used"
    assert row["locked_by"] == "lawrence"
    assert not os.stat(pdir / f"{PID}.yaml").st_mode & stat.S_IWUSR
    assert verify_protocol(PID, ledger, pdir).hash == p.hash


def test_value_change_after_lock_refuses_to_run(pdir, ledger):
    lock_protocol(PID, ledger, "lawrence", pdir)
    _edit(pdir / f"{PID}.yaml", "pooled_net_R_min: 0.0", "pooled_net_R_min: -0.05")
    with pytest.raises(ProtocolHashMismatch):
        verify_protocol(PID, ledger, pdir)


def test_comment_only_change_still_verifies(pdir, ledger):
    lock_protocol(PID, ledger, "lawrence", pdir)
    _edit(pdir / f"{PID}.yaml", "# DRAFT", "# LOCKED 2026-09-27")
    verify_protocol(PID, ledger, pdir)


def test_unlocked_protocol_refuses_to_run(pdir, ledger):
    with pytest.raises(ProtocolNotLocked):
        verify_protocol(PID, ledger, pdir)


def test_relocking_a_changed_file_is_refused(pdir, ledger):
    """Changing a locked protocol needs a new id, never a silent re-lock."""
    first = lock_protocol(PID, ledger, "lawrence", pdir)
    _edit(pdir / f"{PID}.yaml", "dsr_min: 0.95", "dsr_min: 0.80")
    with pytest.raises(ProtocolHashMismatch):
        lock_protocol(PID, ledger, "lawrence", pdir)
    assert ledger.get_protocol(PID)["hash"] == first.hash


def test_ledger_rejects_rewriting_a_locked_hash(pdir, ledger):
    lock_protocol(PID, ledger, "lawrence", pdir)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ledger._conn.execute("UPDATE protocols SET hash = 'x' WHERE id = ?", (PID,))
    # holdout bookkeeping stays mutable (step 4's burn counter writes it)
    ledger._conn.execute("UPDATE protocols SET holdout_evals_used = 1 WHERE id = ?", (PID,))


def test_ledger_never_deletes_trials(pdir, ledger):
    lock_protocol(PID, ledger, "lawrence", pdir)
    c = ledger._conn
    c.execute("INSERT INTO families (id, name, source_type, source_ref, hypothesis, created_by, protocol_id) VALUES ('f1','n','paper','doi:x','h','lawrence',?)", (PID,))
    c.execute("INSERT INTO variants (id, family_id, strategy_hash, params_json, engine_commit, "
              "code_hash, created_by, created_at) VALUES ('v1','f1','sh1','{}','abc','c','lawrence','t')")
    c.execute("INSERT INTO trials (variant_id, instrument, period, metrics_json) "
              "VALUES ('v1','MGC','dev','{}')")
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        c.execute("DELETE FROM trials")


def test_family_requires_a_source_ref(pdir, ledger):
    lock_protocol(PID, ledger, "lawrence", pdir)
    with pytest.raises(sqlite3.IntegrityError):
        ledger._conn.execute("INSERT INTO families (id, name, source_type, source_ref, hypothesis, created_by, protocol_id) VALUES ('f1','n','paper','  ','h','x',?)", (PID,))


def test_agent_can_read_but_not_write_or_lock(pdir, ledger):
    lock_protocol(PID, ledger, "lawrence", pdir)
    api = AgentAPI("claude-code", "model-x", ledger, pdir)
    assert api.get_protocol(PID)["protocol"]["protocol_id"] == PID
    before = (pdir / f"{PID}.yaml").read_bytes()
    with pytest.raises(PermissionError):
        api.write_protocol(PID, "protocol_id: evil\n")
    with pytest.raises(PermissionError):
        api.lock_protocol(PID)
    assert (pdir / f"{PID}.yaml").read_bytes() == before


def test_agent_read_also_goes_through_the_hash_gate(pdir, ledger):
    lock_protocol(PID, ledger, "lawrence", pdir)
    _edit(pdir / f"{PID}.yaml", "max_variants_per_family: 200", "max_variants_per_family: 5000")
    with pytest.raises(ProtocolHashMismatch):
        AgentAPI("claude-code", "model-x", ledger, pdir).get_protocol(PID)


@pytest.mark.parametrize("old,new,msg", [
    ("SIL: {tick: 0.005, tick_value: 5.00", "SIL: {tick: 0.005, tick_value: 25.00", "tick_value"),
    ("universe: [MNQ, MES, MGC, SIL]", "universe: [MNQ, MES, MGC, SIL, MCL]", "without costs"),
    ("holdout_status: partially_used", "holdout_status: fine", "holdout_status"),
    ("protocol_id: metals-intraday-v1", "protocol_id: other", "protocol_id"),
])
def test_lock_refuses_structurally_wrong_protocols(pdir, ledger, old, new, msg):
    """A 5x silver tick_value (SI vs SIL) would make every net R wrong."""
    _edit(pdir / f"{PID}.yaml", old, new)
    with pytest.raises(ProtocolError, match=msg):
        lock_protocol(PID, ledger, "lawrence", pdir)
    assert ledger.get_protocol(PID) is None


def test_protocol_costs_override_broker_commission(pdir, ledger):
    p = lock_protocol(PID, ledger, "lawrence", pdir)
    spec = protocol_cost_spec(p, "SIL")
    assert spec.commission_rt == Decimal("2.00")
    assert spec.point_value == Decimal("1000")
    with pytest.raises(ProtocolError):
        protocol_cost_spec(p, "MCL")


def test_cli_refuses_to_run_on_hash_mismatch(tmp_path, pdir, monkeypatch, caplog):
    from app.backtest.__main__ import _amain
    lg = Ledger(tmp_path / "ledger.db")
    lock_protocol(PID, lg, "lawrence", pdir)
    lg.close()
    _edit(pdir / f"{PID}.yaml", "min_trades_dev_per_instrument: 150",
          "min_trades_dev_per_instrument: 50")
    monkeypatch.chdir(tmp_path)
    rc = asyncio.run(_amain(["--bars", "missing.csv", "--instrument", "MGC",
                             "--protocol", PID, "--ledger", str(tmp_path / "ledger.db")]))
    assert rc == 2
    assert "refusing to run" in caplog.text
    assert not (tmp_path / "backtests").exists()
