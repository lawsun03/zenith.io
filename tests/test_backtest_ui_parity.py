"""UI backtest parity: the UI subprocess (app/backtest.py) must delegate to the
canonical runner.run_backtest while preserving the result-JSON shape the
frontend reads. These tests lock that contract.

NOTE: BacktestConfig takes `bars` (an Iterator[Bar]), not `bars_path`. Each
helper call builds a FRESH iterator because run_backtest consumes it.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import json
import os
import subprocess
import sys

from app.backtest.runner import BacktestConfig, run_backtest
from app.replay import load_bars_csv

FIXTURE = "tests/fixtures/bars_parity_mini.csv"


def _mini_cfg() -> BacktestConfig:
    return BacktestConfig(
        instrument="MNQ",
        bars=load_bars_csv(FIXTURE, instrument="MNQ", timeframe="1min"),
        timeframe="1min",
        enforce_risk_limits=False,
    )


def test_result_carries_fills():
    result = asyncio.run(run_backtest(_mini_cfg()))
    assert hasattr(result, "fills"), "BacktestResult must expose fills"
    assert isinstance(result.fills, list)


def test_result_to_dict_has_frontend_contract_keys():
    import json
    from app.backtest.runner import result_to_dict
    result = asyncio.run(run_backtest(_mini_cfg()))
    d = result_to_dict(result, id="abc", label="lbl",
                       started_at="2026-06-16T00:00:00Z",
                       completed_at="2026-06-16T00:01:00Z")
    for key in ("id", "label", "started_at", "completed_at",
                "stats", "trades", "signals", "fills",
                "config", "bars_processed", "rejected_signals"):
        assert key in d, f"missing top-level key: {key}"
    for key in ("trades", "win_rate", "net_pnl"):
        assert key in d["stats"], f"missing stats key: {key}"
    # Must be JSON-serializable (no Decimal/datetime leaking through):
    json.dumps(d)


def test_cli_subprocess_writes_frontend_json(tmp_path):
    out = tmp_path / "bt"
    cmd = [sys.executable, "-m", "app.backtest",
           "--bars", FIXTURE,
           "--instrument", "MNQ", "--timeframe", "1min",
           "--out-dir", str(out), "--id", "parity_smoke",
           "--label", "parity smoke"]
    r = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ})
    assert r.returncode == 0, r.stderr
    f = out / "parity_smoke.json"
    assert f.exists(), "subprocess must write <id>.json"
    d = json.loads(f.read_text())
    assert d["id"] == "parity_smoke"
    assert d["label"] == "parity smoke"
    for k in ("trades", "win_rate", "net_pnl"):
        assert k in d["stats"]
    # Proves delegation actually happened: the canonical result_to_dict emits a
    # top-level `rejected_signals` key; the legacy hand-built dict does not.
    assert "rejected_signals" in d
