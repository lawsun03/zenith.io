"""Vault + one-shot holdout service (spec §3, §4; build step 4).

WHY: the SI/GC study burned its holdout by looking at 2023–26 over and over
while picking candidates. These tests pin the step 4 acceptance:
(1) the research worker refuses to run when it can list the vault,
(2) a second holdout call on the same strategy is refused (and still logged),
(3) the burn counter flips the protocol to burned at
    holdout_evals_before_burned and stays burned,
plus the ingest split (dev and holdout never share a session, embargo
dropped) and that results carry metrics only, never trades.

Not caught here (fake runner, root on CI):
- that the OS actually denies the worker: tests running as root can list any
  directory, so the real-denial test only runs as a non-root user.
  Acceptance (1) is confirmed on the Windows box with `vault check` run as
  the research user;
- that run_bot_config_variant reproduces dev numbers: the runner is faked,
  because the engine needs project_x_py, which this container lacks.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from app.backtest.metrics import summarize_r
from app.research import identity
from app.research.agent_api import AgentAPI
from app.research.holdout import (
    HoldoutRefused, VaultService, decline, holdout_criteria, pending_requests, request_holdout,
)
from app.research.ledger import Ledger
from app.research.protocol import PROTOCOLS_DIR, ProtocolError, lock_protocol, validate
from app.research.seeds import register_seeds, seed_family_id
from app.research.vault import (
    VaultError, VaultExposed, assert_unreadable, check_dev_split, dev_bars_path,
    holdout_bars_path, ingest,
)

REPO = Path(__file__).resolve().parents[2]
PID = "vault-test-v1"
UNIVERSE = ["MGC", "SIL"]
COMMIT, CODE = "c0ffee" * 6, "c0de" * 16
SENTINEL_PRICE = 1234.5678  # appears in trades only; must never reach a result


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def pdir(tmp_path):
    data = yaml.safe_load((REPO / PROTOCOLS_DIR / "metals-intraday-v1.yaml").read_text())
    data.update(protocol_id=PID, universe=UNIVERSE)
    data["periods"] = {"dev": {"start": date(2022, 12, 1), "end": date(2022, 12, 31)},
                       "embargo_days": 5,
                       "holdout": {"start": date(2023, 1, 6), "end": date(2023, 1, 31)},
                       "holdout_status": "sealed"}
    data["budgets"]["holdout_evals_before_burned"] = 2
    d = tmp_path / "protocols"
    d.mkdir()
    (d / f"{PID}.yaml").write_text(yaml.safe_dump(data))
    return d


@pytest.fixture
def ledger(tmp_path):
    lg = Ledger(tmp_path / "ledger.db")
    yield lg
    lg.close()


@pytest.fixture
def proto(pdir, ledger):
    return lock_protocol(PID, ledger, "lawrence", pdir)


def _write_bars(path: Path, skip: set[date] = frozenset()) -> None:
    """A 14:00 UTC bar each weekday 2022-11-28..2023-02-10, plus a 23:30 UTC
    bar (17:30 CT, so the NEXT session) each Sunday-Thursday, like CME."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        d = date(2022, 11, 28)
        while d <= date(2023, 2, 10):
            times = ([(14, 0)] if d.weekday() < 5 else []) + \
                    ([(23, 30)] if d.weekday() in (6, 0, 1, 2, 3) else [])
            for hh, mm in times if d not in skip else ():
                ts = datetime(d.year, d.month, d.day, hh, mm, tzinfo=timezone.utc)
                w.writerow([ts.isoformat(), "10", "11", "9", "10.5", "5"])
            d += timedelta(days=1)


def _sessions(path: Path) -> set[date]:
    from app.risk.flatten import trading_day_ct
    with path.open() as f:
        return {trading_day_ct(datetime.fromisoformat(r["ts"])) for r in csv.DictReader(f)}


@pytest.fixture
def split(tmp_path, proto):
    bars = tmp_path / "bars"
    _write_bars(bars / "bars_MGC.csv")
    _write_bars(bars / "bars_SIL.csv", skip={date(2023, 1, 1), date(2023, 1, 2)})
    vault, dev = tmp_path / "vault", tmp_path / "dev"
    summary = ingest(proto, bars, dev, vault)
    return {"vault": vault, "dev": dev, "bars": bars, "summary": summary}


class FakeRunner:
    """Stands in for the engine: fixed per-trade net R per instrument."""

    def __init__(self, net_r: float = 0.2, fail: bool = False):
        self.net_r, self.fail, self.calls = net_r, fail, []

    def __call__(self, params, instrument, bars_path, protocol):
        self.calls.append((instrument, bars_path))
        if self.fail:
            raise RuntimeError("engine crashed")
        rows, trades = [], []
        for i in range(20):
            ts = datetime(2023, 1, 9, 15, i, tzinfo=timezone.utc)
            r = {"gross_R": self.net_r + 0.1 + (0.5 if i % 2 else -0.5),
                 "net_R": self.net_r + (0.5 if i % 2 else -0.5),
                 "net_R_stress": self.net_r - 0.05, "cost_R": 0.1, "risk_usd": 50.0}
            rows.append((ts, r))
            trades.append({"entry_ts": ts.isoformat(), "entry_price": SENTINEL_PRICE, **r})
        return {"research_metrics": summarize_r(rows), "trades": trades}


def _register(ledger, params: dict, *, fam="fam", dev_pass=True, dsr=0.97,
              contaminated=False, protocol_id=PID) -> str:
    ledger.execute("INSERT OR IGNORE INTO families (id, name, source_type, source_ref, hypothesis, "
                   "created_by, protocol_id, holdout_contaminated) VALUES (?,?,?,?,?,?,?,?)",
                   (fam, fam, "paper", "doi:10/x", "h", "test", protocol_id, int(contaminated)))
    sh = identity.strategy_hash(COMMIT, CODE, params, UNIVERSE, protocol_id)
    vid = f"v-{sh[:8]}"
    ledger.execute("INSERT INTO variants (id, family_id, strategy_hash, params_json, engine_commit, "
                   "code_hash, created_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
                   (vid, fam, sh, identity.canonical_params(params), COMMIT, CODE, "test", "t"))
    if dev_pass is not None:
        ledger.execute("INSERT INTO trials (variant_id, instrument, period, metrics_json, null_pct, "
                       "dsr, fdr_pass, passed_dev) VALUES (?, 'pooled', 'dev', ?, 0.99, ?, 1, ?)",
                       (vid, json.dumps({"net_R_mean": 0.15}), dsr, int(dev_pass)))
    return sh


def _svc(ledger, split, pdir, runner=None, code=(COMMIT, CODE)):
    return VaultService(ledger, split["vault"], pdir, run_variant=runner or FakeRunner(),
                        current_code=lambda: code)


def _rows(ledger):
    return [dict(r) for r in ledger.execute("SELECT * FROM holdout_evals ORDER BY id")]


# ------------------------------------------------------------ strategy_hash

def test_strategy_hash_ignores_key_order_but_not_any_component():
    base = identity.strategy_hash("c1", "k1", {"a": 1, "b": 2}, ["MGC", "SIL"], "p1")
    assert base == identity.strategy_hash("c1", "k1", {"b": 2, "a": 1}, ["SIL", "MGC"], "p1")
    changed = [identity.strategy_hash("c2", "k1", {"a": 1, "b": 2}, ["MGC", "SIL"], "p1"),
               identity.strategy_hash("c1", "k2", {"a": 1, "b": 2}, ["MGC", "SIL"], "p1"),
               identity.strategy_hash("c1", "k1", {"a": 1, "b": 3}, ["MGC", "SIL"], "p1"),
               identity.strategy_hash("c1", "k1", {"a": 1, "b": 2}, ["MGC"], "p1"),
               identity.strategy_hash("c1", "k1", {"a": 1, "b": 2}, ["MGC", "SIL"], "p2")]
    assert base not in changed and len(set(changed)) == 5


def test_code_hash_is_the_same_on_a_crlf_checkout(tmp_path):
    """Windows core.autocrlf must not make the same commit a different strategy."""
    (tmp_path / "s.py").write_bytes(b"x = 1\ny = 2\n")
    lf = identity.code_hash([tmp_path / "s.py"], root=tmp_path)
    (tmp_path / "s.py").write_bytes(b"x = 1\r\ny = 2\r\n")
    assert identity.code_hash([tmp_path / "s.py"], root=tmp_path) == lf
    (tmp_path / "s.py").write_bytes(b"x = 1\ny = 3\n")
    assert identity.code_hash([tmp_path / "s.py"], root=tmp_path) != lf


# ------------------------------------------------------------ ingest split

def test_split_never_shares_a_session_and_drops_the_embargo(split):
    """Embargo: the first 5 sessions after dev end across the universe
    (Jan 2-6) are in neither file; holdout starts Jan 9, not the protocol's
    Jan 6. SIL has no Jan 2 bars, but MGC does, so both share one embargo."""
    for inst in UNIVERSE:
        dev = _sessions(dev_bars_path(split["dev"], PID, inst))
        ho = _sessions(holdout_bars_path(split["vault"], PID, inst))
        assert max(dev) == date(2022, 12, 30) and min(dev) == date(2022, 12, 1)
        assert min(ho) == date(2023, 1, 9) and max(ho) == date(2023, 1, 31)
        assert not dev & ho
        assert not (dev | ho) & {date(2023, 1, d) for d in range(2, 7)}
    assert split["summary"]["embargo_sessions"] == [f"2023-01-0{d}" for d in range(2, 7)]
    assert split["summary"]["holdout"][0] == "2023-01-07"


def test_manifests_describe_each_side_only(split):
    dev_m = json.loads((split["dev"] / PID / "manifest.json").read_text())
    ho_m = json.loads((split["vault"] / PID / "holdout" / "manifest.json").read_text())
    assert dev_m["instruments"]["MGC"]["sessions"] == ["2022-12-01", "2022-12-30"]
    assert ho_m["instruments"]["MGC"]["sessions"] == ["2023-01-09", "2023-01-31"]


def test_dev_check_passes_on_the_split_and_flags_holdout_bars_in_dev(split, proto):
    assert check_dev_split(proto, split["dev"]) == []
    with dev_bars_path(split["dev"], PID, "SIL").open("a", newline="") as f:
        csv.writer(f).writerow(["2023-01-10T15:00:00+00:00", "1", "1", "1", "1", "1"])
    assert check_dev_split(proto, split["dev"]) == [
        "SIL: dev file reaches session 2023-01-10, after dev end 2022-12-31"]


def test_ingest_refuses_to_overwrite_a_split(split, proto):
    with pytest.raises(VaultError, match="already exists"):
        ingest(proto, split["bars"], split["dev"], split["vault"])
    ingest(proto, split["bars"], split["dev"], split["vault"], replace=True)


# ------------------------------------------------------------ isolation guard

def test_worker_refuses_to_start_when_it_can_list_the_vault(tmp_path, ledger):
    """Acceptance (1): if the worker can read vault/, the setup is wrong."""
    (tmp_path / "vault").mkdir()
    with pytest.raises(VaultExposed):
        AgentAPI("claude-code", "m", ledger, vault_dir=tmp_path / "vault")


def test_isolation_check_passes_only_on_permission_denied(tmp_path, monkeypatch):
    def denied(_):
        raise PermissionError("Access is denied")
    with pytest.raises(VaultError, match="does not exist"):
        assert_unreadable(tmp_path / "typo")
    monkeypatch.setattr(os, "listdir", denied)
    assert_unreadable(tmp_path / "vault")


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="root lists every directory; real denial needs a normal user")
def test_real_os_denial_passes_the_check(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    os.chmod(v, 0)
    try:
        assert_unreadable(v)
    finally:
        os.chmod(v, 0o700)


def test_vault_service_needs_to_read_the_vault(tmp_path, ledger, monkeypatch):
    def denied(_):
        raise PermissionError("Access is denied")
    monkeypatch.setattr(os, "listdir", denied)
    with pytest.raises(VaultError, match="can't read the vault"):
        VaultService(ledger, tmp_path)


# ------------------------------------------------------------ one-shot rule

def test_second_holdout_call_on_a_strategy_is_refused_and_logged(split, pdir, ledger):
    """Acceptance (2). Agent request -> queue -> human release, then every
    further call (agent or human) on that strategy is refused, and each
    refusal is a ledger row."""
    sh = _register(ledger, {"k": 1})
    req = request_holdout(ledger, sh, PID, "claude-code", "agent", "model-x", pdir)
    assert pending_requests(ledger)[0]["id"] == req
    with pytest.raises(HoldoutRefused, match="already pending"):
        request_holdout(ledger, sh, PID, "kimi", "agent", "k3", pdir)

    result = _svc(ledger, split, pdir).release(req, approved_by="lawrence")
    assert result["holdout_evals_used"] == 1
    with pytest.raises(HoldoutRefused, match="already evaluated"):
        request_holdout(ledger, sh, PID, "claude-code", "agent", "model-x", pdir)
    with pytest.raises(HoldoutRefused, match="already evaluated"):
        _svc(ledger, split, pdir).evaluate(sh, PID, "lawrence")

    rows = _rows(ledger)
    assert [r["status"] for r in rows] == ["evaluated", "refused", "refused", "refused"]
    assert rows[0]["requested_by"] == "claude-code" and rows[0]["model_id"] == "model-x"
    assert rows[0]["approved_by"] == "lawrence" and rows[0]["caller_kind"] == "agent"
    assert rows[3]["caller_kind"] == "human"
    assert ledger.get_protocol(PID)["holdout_evals_used"] == 1


def test_agent_api_request_holdout_only_queues(split, pdir, ledger, monkeypatch):
    def denied(_):
        raise PermissionError("Access is denied")
    sh = _register(ledger, {"k": 1})
    with monkeypatch.context() as m:
        m.setattr(os, "listdir", denied)
        api = AgentAPI("claude-code", "model-x", ledger, pdir, vault_dir=split["vault"])
    assert api.request_holdout(sh, PID) == {"eval_id": 1, "status": "pending"}
    assert ledger.get_protocol(PID)["holdout_evals_used"] == 0


def test_database_backstops_the_one_shot_rule(split, pdir, ledger):
    sh = _register(ledger, {"k": 1})
    _svc(ledger, split, pdir).evaluate(sh, PID, "lawrence")
    with pytest.raises(sqlite3.IntegrityError):
        ledger.execute("INSERT INTO holdout_evals (strategy_hash, protocol_id, requested_by, "
                       "caller_kind, ts, status) VALUES (?, ?, 'x', 'human', 't', 'pending')",
                       (sh, PID))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        ledger.execute("UPDATE holdout_evals SET passed = 1 - passed")
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        ledger.execute("DELETE FROM holdout_evals")


def test_declined_request_frees_the_slot_without_counting(split, pdir, ledger):
    sh = _register(ledger, {"k": 1})
    req = request_holdout(ledger, sh, PID, "grok", "agent", "g", pdir)
    decline(ledger, req, "lawrence")
    assert ledger.get_protocol(PID)["holdout_evals_used"] == 0
    request_holdout(ledger, sh, PID, "grok", "agent", "g", pdir)


# ------------------------------------------------------------ burn counter

def test_burn_counter_flips_to_burned_and_stays_burned(split, pdir, ledger):
    """Acceptance (3), with holdout_evals_before_burned = 2."""
    svc = _svc(ledger, split, pdir)
    assert ledger.get_protocol(PID)["holdout_status"] == "sealed"
    r1 = svc.evaluate(_register(ledger, {"k": 1}), PID, "lawrence")
    assert (r1["holdout_evals_used"], r1["holdout_status"]) == (1, "partially_used")
    third = _register(ledger, {"k": 3})
    queued = request_holdout(ledger, third, PID, "claude-code", "agent", "m", pdir)
    r2 = svc.evaluate(_register(ledger, {"k": 2}), PID, "lawrence")
    assert (r2["holdout_evals_used"], r2["holdout_status"]) == (2, "burned")

    with pytest.raises(HoldoutRefused, match="burned"):
        svc.release(queued, "lawrence")  # queued before the burn, released after
    with pytest.raises(HoldoutRefused, match="burned"):
        request_holdout(ledger, _register(ledger, {"k": 4}), PID, "claude-code", "agent", "m", pdir)
    assert ledger.get_protocol(PID)["holdout_evals_used"] == 2
    with pytest.raises(sqlite3.IntegrityError, match="stays burned"):
        ledger.execute("UPDATE protocols SET holdout_status = 'sealed' WHERE id = ?", (PID,))
    with pytest.raises(sqlite3.IntegrityError, match="never decreases"):
        ledger.execute("UPDATE protocols SET holdout_evals_used = 0 WHERE id = ?", (PID,))


# ------------------------------------------------------------ refusals that don't count

@pytest.mark.parametrize("kwargs, match", [
    ({"dev_pass": None}, "not passed dev"),
    ({"dev_pass": False}, "not passed dev"),
    ({"dsr": None}, "not passed dev"),  # passed_dev without the luck columns
    ({"contaminated": True}, "holdout_contaminated"),
])
def test_strategies_not_eligible_are_refused(split, pdir, ledger, kwargs, match):
    sh = _register(ledger, {"k": 1}, **kwargs)
    with pytest.raises(HoldoutRefused, match=match):
        _svc(ledger, split, pdir).evaluate(sh, PID, "lawrence")
    assert ledger.get_protocol(PID)["holdout_evals_used"] == 0


def test_unknown_strategy_is_refused(split, pdir, ledger):
    with pytest.raises(HoldoutRefused, match="unknown strategy_hash"):
        request_holdout(ledger, "f" * 64, PID, "claude-code", "agent", "m", pdir)


def test_code_changed_since_dev_is_refused_without_reading_bars(split, pdir, ledger):
    sh = _register(ledger, {"k": 1})
    runner = FakeRunner()
    with pytest.raises(HoldoutRefused, match="changed since dev"):
        _svc(ledger, split, pdir, runner, code=("beef" * 10, CODE)).evaluate(sh, PID, "lawrence")
    assert runner.calls == []
    assert ledger.get_protocol(PID)["holdout_evals_used"] == 0


def test_crashed_evaluation_is_refused_and_not_counted(split, pdir, ledger):
    sh = _register(ledger, {"k": 1})
    with pytest.raises(HoldoutRefused, match="evaluation error"):
        _svc(ledger, split, pdir, FakeRunner(fail=True)).evaluate(sh, PID, "lawrence")
    assert ledger.get_protocol(PID)["holdout_evals_used"] == 0


# ------------------------------------------------------------ results

def test_result_is_metrics_only_from_vault_bars(split, pdir, ledger):
    runner = FakeRunner()
    sh = _register(ledger, {"k": 1})
    result = _svc(ledger, split, pdir, runner).evaluate(sh, PID, "lawrence")
    assert [c[1] for c in runner.calls] == [holdout_bars_path(split["vault"], PID, i)
                                           for i in UNIVERSE]
    stored = _rows(ledger)[0]["metrics_json"]
    for blob in (json.dumps(result), stored):
        assert str(SENTINEL_PRICE) not in blob and '"trades"' not in blob
    m = result["metrics"]
    assert set(m["per_instrument"]) == set(UNIVERSE)
    assert m["pooled"]["n_trades"] == 40
    assert m["pooled"]["net_R_mean"] == pytest.approx(0.2)


def test_holdout_pass_needs_net_positive_and_no_big_shortfall_vs_dev(proto):
    """dev mean 0.5; holdout mean 0.1, SE 0.1 -> floor 0.3 -> fail even though
    net positive. Holdout mean 0.35 clears it."""
    lo = [0.1 + (0.1 * 5 ** 0.5 if i % 2 else -0.1 * 5 ** 0.5) for i in range(4)]
    c = holdout_criteria(proto, lo, 0.5)
    assert c["net_R_mean"] > 0 and not c["passed"]
    assert holdout_criteria(proto, [x + 0.25 for x in lo], 0.5)["passed"]
    assert not holdout_criteria(proto, [-0.1, -0.1, -0.1], -0.2)["passed"]  # net_R_min


# ------------------------------------------------------------ seeds, protocol, migration

def test_seed_candidates_register_as_contaminated_and_are_refused(split, pdir, ledger):
    assert len(register_seeds(ledger, PID, "lawrence")) == 2
    assert register_seeds(ledger, PID, "lawrence") == []
    rows = ledger.execute("SELECT id, holdout_contaminated, source_ref FROM families").fetchall()
    assert {r["id"] for r in rows} == {seed_family_id("gold-pdhl-sweep", PID),
                                       seed_family_id("silver-825-orb", PID)}
    assert all(r["holdout_contaminated"] == 1 and r["source_ref"] for r in rows)
    sh = _register(ledger, {"seed": 1}, fam=seed_family_id("silver-825-orb", PID))
    with pytest.raises(HoldoutRefused, match="holdout_contaminated"):
        request_holdout(ledger, sh, PID, "lawrence", "human", None, pdir)


def test_protocol_must_keep_one_holdout_eval_per_strategy():
    data = yaml.safe_load((REPO / PROTOCOLS_DIR / "metals-intraday-v1.yaml").read_text())
    data["budgets"]["holdout_evals_per_strategy"] = 2
    with pytest.raises(ProtocolError, match="must be 1"):
        validate(data, "metals-intraday-v1")


def test_v1_ledger_migrates_and_keeps_its_rows(tmp_path):
    path = tmp_path / "v1.db"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE protocols (id TEXT PRIMARY KEY, hash TEXT NOT NULL, yaml TEXT NOT NULL,
            locked_at TEXT NOT NULL, locked_by TEXT NOT NULL, holdout_status TEXT NOT NULL,
            holdout_evals_used INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE families (id TEXT PRIMARY KEY, name TEXT NOT NULL, source_type TEXT NOT NULL,
            source_ref TEXT NOT NULL, hypothesis TEXT NOT NULL, created_by TEXT NOT NULL,
            protocol_id TEXT NOT NULL);
        CREATE TABLE variants (id TEXT PRIMARY KEY, family_id TEXT NOT NULL,
            strategy_hash TEXT NOT NULL UNIQUE, params_json TEXT NOT NULL,
            engine_commit TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE holdout_evals (id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_hash TEXT NOT NULL, protocol_id TEXT NOT NULL, requested_by TEXT NOT NULL,
            approved_by TEXT, ts TEXT NOT NULL, metrics_json TEXT, passed INTEGER,
            UNIQUE (strategy_hash, protocol_id));
        CREATE TRIGGER holdout_evals_no_delete BEFORE DELETE ON holdout_evals
        BEGIN SELECT RAISE(ABORT, 'holdout evaluations are never deleted'); END;
        INSERT INTO protocols VALUES ('p', 'h', 'y', 't', 'l', 'sealed', 0);
        INSERT INTO holdout_evals (strategy_hash, protocol_id, requested_by, approved_by, ts,
            metrics_json, passed) VALUES ('s', 'p', 'lawrence', 'lawrence', 't', '{}', 1);
        PRAGMA user_version = 1;
    """)
    c.close()
    lg = Ledger(path)
    assert lg.execute("PRAGMA user_version").fetchone()[0] == 2
    row = lg.execute("SELECT * FROM holdout_evals").fetchone()
    assert (row["status"], row["caller_kind"], row["passed"]) == ("evaluated", "human", 1)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        lg.execute("UPDATE holdout_evals SET passed = 0")
    with pytest.raises(sqlite3.IntegrityError, match="never decreases"):
        lg.execute("UPDATE protocols SET holdout_evals_used = -1")
    lg.close()
