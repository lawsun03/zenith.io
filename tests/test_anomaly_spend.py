"""Grok spend-cap enforcement and persistence — mirrors
tests/test_ledger.py's treatment of research.stats.budget.TrialBudgetExceeded:
raise before the write that would breach the cap, not a warning."""
from __future__ import annotations

import pytest

from research.anomaly.spend import GrokBudgetExceeded, SpendLedger


def test_fresh_ledger_at_a_missing_path_starts_at_zero(tmp_path):
    ledger = SpendLedger.load(tmp_path / "spend.json")
    assert ledger.total_usd == 0.0


def test_charge_accumulates(tmp_path):
    ledger = SpendLedger.load(tmp_path / "spend.json")
    ledger.charge(1.5, cap=10.0)
    ledger.charge(2.5, cap=10.0)
    assert ledger.total_usd == pytest.approx(4.0)


def test_charge_persists_across_reloads(tmp_path):
    path = tmp_path / "spend.json"
    ledger = SpendLedger.load(path)
    ledger.charge(3.0, cap=10.0)

    reloaded = SpendLedger.load(path)
    assert reloaded.total_usd == pytest.approx(3.0)


def test_charge_exceeding_cap_raises(tmp_path):
    ledger = SpendLedger.load(tmp_path / "spend.json")
    ledger.charge(9.0, cap=10.0)
    with pytest.raises(GrokBudgetExceeded):
        ledger.charge(1.5, cap=10.0)


def test_a_rejected_charge_is_not_recorded(tmp_path):
    """The call that would have breached the cap never happened — the
    ledger must not say it did, or a retry-after-fixing-the-cap would
    silently double-count it."""
    path = tmp_path / "spend.json"
    ledger = SpendLedger.load(path)
    ledger.charge(9.0, cap=10.0)
    with pytest.raises(GrokBudgetExceeded):
        ledger.charge(1.5, cap=10.0)
    assert ledger.total_usd == pytest.approx(9.0)
    assert SpendLedger.load(path).total_usd == pytest.approx(9.0)


def test_charge_exactly_at_the_cap_is_allowed(tmp_path):
    ledger = SpendLedger.load(tmp_path / "spend.json")
    ledger.charge(10.0, cap=10.0)
    assert ledger.total_usd == pytest.approx(10.0)
