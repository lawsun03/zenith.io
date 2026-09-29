"""One-shot holdout evaluation (spec §3). The spec's `vault_service`.

Two halves, split by who can run them:

- request_holdout() needs only the ledger. The research worker (AgentAPI)
  calls it; it records a `pending` row or a `refused` one. It never touches
  bars.
- VaultService needs the vault. It runs only in a process that can read
  the vault directory: the human's CLI now, the UI's approvals queue in
  step 8. evaluate() is the human's direct call; release() evaluates a
  pending agent request. The OS enforces the split: an agent can't release
  its own request because it can't read the bars.

Every call leaves one holdout_evals row, whatever the outcome. Only an
`evaluated` row counts towards holdout_evals_before_burned. A refusal,
decline or crashed evaluation doesn't, because nothing about the holdout
left the vault.

Results are §6 metrics per instrument plus pooled, and the §2 holdout pass
criteria. No bars, trades or equity curves (graduated strategies may get
more in step 8).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.backtest.metrics import summarize_r
from app.backtest.prop_sim import load_prop_rules
from app.research import identity
from app.research.ledger import Ledger
from app.research.protocol import PROTOCOLS_DIR, LockedProtocol, ProtocolError, verify_protocol
from app.research.vault import VaultError, holdout_bars_path

log = logging.getLogger(__name__)

HUMAN, AGENT = "human", "agent"
_R_KEYS = ("gross_R", "net_R", "net_R_stress", "cost_R", "risk_usd")

# (params, instrument, bars_path, protocol) -> {"research_metrics": §6 dict,
# "trades": trades carrying the _R_KEYS, as research_metrics writes them}
RunVariant = Callable[[dict, str, Path, LockedProtocol], dict]


class HoldoutRefused(Exception):
    def __init__(self, reason: str, eval_id: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.eval_id = eval_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def dev_result(ledger: Ledger, strategy_hash: str) -> dict | None:
    """Pooled dev metrics if the strategy passed dev, else None.

    Stub until step 5: reads the latest `trials` row with instrument
    'pooled' and period 'dev'. It passes only when passed_dev and fdr_pass
    are set and dsr is filled in, so a hand-set passed_dev without the luck
    columns doesn't count. Nothing writes that row yet, so today every
    strategy is refused here (fails closed)."""
    row = ledger.execute(
        "SELECT t.* FROM trials t JOIN variants v ON v.id = t.variant_id "
        "WHERE v.strategy_hash = ? AND t.instrument = 'pooled' AND t.period = 'dev' "
        "ORDER BY t.id DESC LIMIT 1", (strategy_hash,)).fetchone()
    if row is None or row["passed_dev"] != 1 or row["fdr_pass"] != 1 or row["dsr"] is None:
        return None
    metrics = json.loads(row["metrics_json"])
    return metrics if metrics.get("net_R_mean") is not None else None


def _variant(ledger: Ledger, strategy_hash: str):
    return ledger.execute(
        "SELECT v.*, f.protocol_id AS family_protocol, f.holdout_contaminated, f.id AS fam "
        "FROM variants v JOIN families f ON f.id = v.family_id WHERE v.strategy_hash = ?",
        (strategy_hash,)).fetchone()


def _refusal(ledger: Ledger, strategy_hash: str, protocol_id: str,
             exclude_id: int | None = None) -> str | None:
    proto = ledger.get_protocol(protocol_id)
    if proto["holdout_status"] == "burned":
        return (f"holdout for {protocol_id} is burned ({proto['holdout_evals_used']} evals); "
                "use a new protocol with a later holdout, or a forward test")
    dup = ledger.execute(
        "SELECT id, status FROM holdout_evals WHERE strategy_hash = ? AND protocol_id = ? "
        "AND status IN ('pending','evaluated') AND id IS NOT ?",
        (strategy_hash, protocol_id, exclude_id)).fetchone()
    if dup:
        return f"strategy already {dup['status']} under {protocol_id} (eval {dup['id']}); one shot per strategy"
    v = _variant(ledger, strategy_hash)
    if v is None:
        return "unknown strategy_hash: no registered variant"
    if v["family_protocol"] != protocol_id:
        return f"family {v['fam']} is registered under {v['family_protocol']}, not {protocol_id}"
    if v["holdout_contaminated"]:
        return f"family {v['fam']} is holdout_contaminated; its clean test is the forward test"
    if dev_result(ledger, strategy_hash) is None:
        return "strategy has not passed dev (no passing pooled dev trial with DSR and FDR)"
    return None


def _insert(ledger: Ledger, strategy_hash: str, protocol_id: str, requested_by: str,
            caller_kind: str, model_id: str | None, status: str, reason: str | None) -> int:
    cur = ledger.execute(
        "INSERT INTO holdout_evals (strategy_hash, protocol_id, requested_by, caller_kind, "
        "model_id, ts, status, reason, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (strategy_hash, protocol_id, requested_by, caller_kind, model_id, _now(), status,
         reason, _now() if status != "pending" else None))
    return cur.lastrowid


def request_holdout(ledger: Ledger, strategy_hash: str, protocol_id: str, requested_by: str,
                    caller_kind: str, model_id: str | None = None,
                    protocols_dir: Path = PROTOCOLS_DIR) -> int:
    """Queue a holdout evaluation. Returns the pending eval id or raises
    HoldoutRefused after recording the refusal. An unlocked or edited
    protocol raises ProtocolError with no row, since there is no locked
    protocol to attach it to."""
    verify_protocol(protocol_id, ledger, protocols_dir)
    with ledger.tx():
        reason = _refusal(ledger, strategy_hash, protocol_id)
        eval_id = _insert(ledger, strategy_hash, protocol_id, requested_by, caller_kind,
                          model_id, "refused" if reason else "pending", reason)
    if reason:
        log.warning("holdout request %d refused (%s %s, %s): %s", eval_id, caller_kind,
                    requested_by, strategy_hash[:12], reason)
        raise HoldoutRefused(reason, eval_id)
    log.info("holdout request %d queued for approval: %s under %s by %s %s", eval_id,
             strategy_hash[:12], protocol_id, caller_kind, requested_by)
    return eval_id


def pending_requests(ledger: Ledger) -> list[dict]:
    return [dict(r) for r in ledger.execute(
        "SELECT id, strategy_hash, protocol_id, requested_by, caller_kind, model_id, ts "
        "FROM holdout_evals WHERE status = 'pending' ORDER BY id")]


def _decide(ledger: Ledger, eval_id: int, status: str, approved_by: str,
            reason: str | None = None, metrics: dict | None = None,
            passed: bool | None = None) -> None:
    cur = ledger.execute(
        "UPDATE holdout_evals SET status = ?, reason = ?, approved_by = ?, decided_at = ?, "
        "metrics_json = ?, passed = ? WHERE id = ? AND status = 'pending'",
        (status, reason, approved_by, _now(), json.dumps(metrics) if metrics else None,
         None if passed is None else int(passed), eval_id))
    if cur.rowcount != 1:
        raise ValueError(f"holdout eval {eval_id} is not pending")


def decline(ledger: Ledger, eval_id: int, declined_by: str) -> None:
    _decide(ledger, eval_id, "declined", declined_by, reason=f"declined by {declined_by}")
    log.info("holdout request %d declined by %s", eval_id, declined_by)


def run_bot_config_variant(params: dict, instrument: str, bars_path: Path,
                           protocol: LockedProtocol) -> dict:
    """Default runner: params_json is a BotConfig dump, run through the same
    path as `python -m app.backtest --protocol` (strict fills, protocol
    costs). Step 7's run_dev must use this same function for dev trials,
    or dev and holdout would test different code paths."""
    from app.backtest.__main__ import _run_backtest
    from app.bot_config import BotConfig

    cfg = BotConfig.model_validate(params)
    timeframe = cfg.timeframes[0] if cfg.timeframes else "1min"
    res = asyncio.run(_run_backtest(cfg, bars_path, instrument, timeframe,
                                    strict_fills=True, protocol=protocol))
    return {"research_metrics": res["research_metrics"], "trades": res["trades"]}


def holdout_criteria(protocol: LockedProtocol, pooled_net_r: list[float],
                     dev_net_r_mean: float) -> dict:
    """§2 pass_criteria_holdout: mean >= net_R_min and
    mean >= dev mean - max_shortfall_vs_dev_se * SE(holdout)."""
    c = protocol.data["pass_criteria_holdout"]
    n = len(pooled_net_r)
    mean = statistics.fmean(pooled_net_r) if n else None
    se = statistics.stdev(pooled_net_r) / math.sqrt(n) if n >= 2 else None
    floor = dev_net_r_mean - float(c["max_shortfall_vs_dev_se"]) * se if se is not None else None
    passed = (floor is not None and mean >= float(c["net_R_min"]) and mean >= floor)
    return {"n_trades": n, "net_R_mean": mean, "net_R_min": float(c["net_R_min"]),
            "dev_net_R_mean": dev_net_r_mean, "holdout_se": se, "shortfall_floor": floor,
            "passed": passed}


class VaultService:
    def __init__(self, ledger: Ledger, vault_dir: Path, protocols_dir: Path = PROTOCOLS_DIR,
                 run_variant: RunVariant = run_bot_config_variant,
                 current_code: Callable[[], tuple[str, str]] | None = None) -> None:
        try:
            os.listdir(vault_dir)
        except OSError as e:
            raise VaultError(f"this process can't read the vault at {vault_dir} ({e}); "
                             "run the holdout service as the vault's owner") from e
        self._ledger = ledger
        self._vault_dir = Path(vault_dir)
        self._protocols_dir = protocols_dir
        self._run_variant = run_variant
        self._current_code = current_code or (lambda: (identity.engine_commit(),
                                                       identity.code_hash()))

    def evaluate(self, strategy_hash: str, protocol_id: str, requested_by: str) -> dict:
        """The human's direct call: request and release in one step."""
        eval_id = request_holdout(self._ledger, strategy_hash, protocol_id, requested_by,
                                  HUMAN, protocols_dir=self._protocols_dir)
        return self.release(eval_id, approved_by=requested_by)

    def release(self, eval_id: int, approved_by: str) -> dict:
        row = self._ledger.execute("SELECT * FROM holdout_evals WHERE id = ?",
                                   (eval_id,)).fetchone()
        if row is None or row["status"] != "pending":
            raise ValueError(f"holdout eval {eval_id} is not pending")
        sh, pid = row["strategy_hash"], row["protocol_id"]

        def refuse(reason: str) -> HoldoutRefused:
            _decide(self._ledger, eval_id, "refused", approved_by, reason=reason)
            log.warning("holdout eval %d refused at release by %s: %s", eval_id, approved_by, reason)
            return HoldoutRefused(reason, eval_id)

        try:
            protocol = verify_protocol(pid, self._ledger, self._protocols_dir)
        except ProtocolError as e:
            raise refuse(str(e))
        reason = _refusal(self._ledger, sh, pid, exclude_id=eval_id)
        variant = _variant(self._ledger, sh)
        if reason is None:
            try:
                commit, code = self._current_code()
            except (identity.DirtyTree, OSError) as e:
                reason = f"can't identify the current code: {e}"
            else:
                now = identity.strategy_hash(commit, code, json.loads(variant["params_json"]),
                                             protocol.data["universe"], pid)
                if now != sh:
                    reason = (f"current checkout hashes to {now[:12]}, not {sh[:12]}: engine "
                              f"(dev ran {variant['engine_commit'][:12]}, now {commit[:12]}) or "
                              "strategy code changed since dev")
        if reason is None:
            missing = [i for i in protocol.data["universe"]
                       if not holdout_bars_path(self._vault_dir, pid, i).exists()]
            if missing:
                reason = f"no holdout bars for {missing} in the vault; run vault ingest"
        if reason:
            raise refuse(reason)

        log.info("holdout eval %d released by %s: running %s under %s", eval_id, approved_by,
                 sh[:12], pid)
        try:
            metrics = self._evaluate(protocol, json.loads(variant["params_json"]),
                                     dev_result(self._ledger, sh))
        except Exception as e:
            log.exception("holdout eval %d failed", eval_id)
            raise refuse(f"evaluation error: {e!r}")

        with self._ledger.tx():
            proto = self._ledger.get_protocol(pid)
            if proto["holdout_status"] == "burned":
                burned = True
            else:
                burned = False
                used = proto["holdout_evals_used"] + 1
                limit = int(protocol.data["budgets"]["holdout_evals_before_burned"])
                status = ("burned" if used >= limit else
                          "partially_used" if proto["holdout_status"] == "sealed" else
                          proto["holdout_status"])
                _decide(self._ledger, eval_id, "evaluated", approved_by, metrics=metrics,
                        passed=metrics["criteria"]["passed"])
                self._ledger.execute("UPDATE protocols SET holdout_evals_used = ?, "
                                     "holdout_status = ? WHERE id = ?", (used, status, pid))
        if burned:  # burned by another eval while this one ran; its metrics are discarded
            raise refuse(f"holdout for {pid} was burned while this evaluation ran")
        log.info("holdout eval %d: passed=%s, %s now %d/%d used (%s)", eval_id,
                 metrics["criteria"]["passed"], pid, used, limit, status)
        if status == "burned":
            log.warning("holdout for protocol %s is now BURNED (%d evals)", pid, used)
        return {"eval_id": eval_id, "strategy_hash": sh, "protocol_id": pid,
                "passed": metrics["criteria"]["passed"], "metrics": metrics,
                "holdout_evals_used": used, "holdout_status": status}

    def _evaluate(self, protocol: LockedProtocol, params: dict, dev: dict) -> dict:
        prop_cfg = protocol.data["prop_sim"]
        mll = load_prop_rules(prop_cfg["account"]).combine.mll_distance
        per_inst: dict[str, dict] = {}
        rows = []
        for inst in protocol.data["universe"]:
            out = self._run_variant(params, inst,
                                    holdout_bars_path(self._vault_dir, protocol.id, inst), protocol)
            m = out["research_metrics"]
            if "error" in m:
                raise RuntimeError(f"{inst}: research metrics failed: {m['error']}")
            per_inst[inst] = m
            rows += [(datetime.fromisoformat(t["entry_ts"]), {k: t[k] for k in _R_KEYS})
                     for t in out["trades"] if "net_R" in t]
        pooled = summarize_r(rows, float(protocol.data["cost_stress_multiplier"]), mll)
        criteria = holdout_criteria(protocol, [r["net_R"] for _, r in rows],
                                    float(dev["net_R_mean"]))
        return {"per_instrument": per_inst, "pooled": pooled, "criteria": criteria}
