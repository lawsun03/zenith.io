"""Defining-behavior tests for funded_sim.py --save-id registry output (B10).

Verifies that _save_funded_result writes a valid backtests/<id>.json that the
BacktestsPage UI can render, with both funded_pipeline.combine and
funded_pipeline.xfa blocks populated from the simulation results.
"""
from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.funded_sim import _save_funded_result


def _make_curve(n_days: int = 5, daily_pnl: Decimal = Decimal("100")) -> list[tuple[datetime, Decimal]]:
    """Synthetic equity curve: n_days of constant daily_pnl."""
    base = Decimal("50000")
    return [
        (datetime(2024, 1, d + 1, 16, 0, 0, tzinfo=timezone.utc), base + daily_pnl * (d + 1))
        for d in range(n_days)
    ]


def _default_args(**kwargs) -> Namespace:
    return Namespace(
        save_id=kwargs.get("save_id", "test_funded_run"),
        save_label=kwargs.get("save_label", None),
        instrument=kwargs.get("instrument", "MNQ"),
        timeframe=kwargs.get("timeframe", "5min"),
    )


def test_save_funded_result_creates_valid_json(tmp_path: Path) -> None:
    """Written JSON must be valid and contain the required top-level fields."""
    curve = _make_curve()
    combine_stats = {"attempts": 5, "passes": 3, "busts": 1, "median_days_to_pass": 4}
    xfa_stats = {
        "accounts": 3, "busts": 1,
        "gross_payouts": Decimal("6000"), "net_payouts": Decimal("5400"),
        "median_days_to_first_payout": 7,
    }
    _save_funded_result(
        Path("research/equity_test.csv"), curve,
        _default_args(), combine_stats, xfa_stats,
        Decimal("200"), _out_dir=tmp_path,
    )
    out = tmp_path / "test_funded_run.json"
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["id"] == "test_funded_run"
    assert data["instrument"] == "MNQ"
    assert data["funded_pipeline"]["combine"]["passes"] == 3
    assert data["funded_pipeline"]["xfa"]["busts"] == 1
    assert "daily granularity" in data["funded_pipeline"]["caveat"]
    assert "haircut $200" in data["funded_pipeline"]["caveat"]


def test_save_funded_result_equity_curve_in_stats(tmp_path: Path) -> None:
    """The equity curve must appear in stats.equity_curve so the chart renders."""
    curve = _make_curve(n_days=3)
    combine_stats = {"attempts": 1, "passes": 0, "busts": 0, "median_days_to_pass": None}
    xfa_stats = {
        "accounts": 1, "busts": 0,
        "gross_payouts": Decimal("0"), "net_payouts": Decimal("0"),
        "median_days_to_first_payout": None,
    }
    _save_funded_result(
        Path("equity.csv"), curve,
        _default_args(save_id="curve_test"), combine_stats, xfa_stats,
        Decimal("0"), _out_dir=tmp_path,
    )
    data = json.loads((tmp_path / "curve_test.json").read_text())
    ec = data["stats"]["equity_curve"]
    assert len(ec) == 3
    assert ec[0][1] == "50100"   # base + 1 * 100
    assert ec[2][1] == "50300"   # base + 3 * 100


def test_save_funded_result_xfa_payouts_as_strings(tmp_path: Path) -> None:
    """gross_payouts and net_payouts must be serialised as strings (Decimal-safe)."""
    curve = _make_curve(n_days=1)
    xfa_stats = {
        "accounts": 2, "busts": 1,
        "gross_payouts": Decimal("12345.50"), "net_payouts": Decimal("11111.00"),
        "median_days_to_first_payout": 10,
    }
    combine_stats = {"attempts": 2, "passes": 1, "busts": 1, "median_days_to_pass": 5}
    _save_funded_result(
        Path("eq.csv"), curve,
        _default_args(save_id="payout_str"), combine_stats, xfa_stats,
        Decimal("0"), _out_dir=tmp_path,
    )
    data = json.loads((tmp_path / "payout_str.json").read_text())
    xfa = data["funded_pipeline"]["xfa"]
    assert isinstance(xfa["gross_payouts"], str)
    assert isinstance(xfa["net_payouts"], str)
    assert xfa["gross_payouts"] == "12345.50"


def test_save_funded_result_save_label_override(tmp_path: Path) -> None:
    """When --save-label is provided it overrides the default label."""
    curve = _make_curve(n_days=1)
    combine_stats = {"attempts": 1, "passes": 0, "busts": 0, "median_days_to_pass": None}
    xfa_stats = {
        "accounts": 1, "busts": 0,
        "gross_payouts": Decimal("0"), "net_payouts": Decimal("0"),
        "median_days_to_first_payout": None,
    }
    _save_funded_result(
        Path("equity.csv"), curve,
        _default_args(save_id="label_test", save_label="My Custom Label"),
        combine_stats, xfa_stats, Decimal("0"), _out_dir=tmp_path,
    )
    data = json.loads((tmp_path / "label_test.json").read_text())
    assert data["label"] == "My Custom Label"


def test_save_funded_result_id_sanitised(tmp_path: Path) -> None:
    """Special characters in --save-id are replaced with underscores."""
    curve = _make_curve(n_days=1)
    combine_stats = {"attempts": 1, "passes": 0, "busts": 0, "median_days_to_pass": None}
    xfa_stats = {
        "accounts": 1, "busts": 0,
        "gross_payouts": Decimal("0"), "net_payouts": Decimal("0"),
        "median_days_to_first_payout": None,
    }
    _save_funded_result(
        Path("equity.csv"), curve,
        _default_args(save_id="orb r1.0 / h200"),
        combine_stats, xfa_stats, Decimal("0"), _out_dir=tmp_path,
    )
    # slashes and spaces and dots become underscores
    out = tmp_path / "orb_r1_0___h200.json"
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["id"] == "orb_r1_0___h200"
