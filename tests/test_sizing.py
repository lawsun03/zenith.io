"""Tests for risk-based position sizing (pure function)."""
from decimal import Decimal

from app.risk.sizing import risk_based_size


# MGC point value = $10/pt. $50k equity, 0.25% = $125 budget.
EQUITY = Decimal("50000")
PCT = Decimal("0.25")
PV = Decimal("10")
MAXSZ = 30


def test_normal_stop_sizes_down_from_budget():
    """3.0pt stop -> $30/contract; $125 budget // $30 = 4 contracts."""
    assert risk_based_size(EQUITY, PCT, Decimal("3.0"), PV, MAXSZ) == 4


def test_wide_stop_caps_risk():
    """10.8pt stop -> $108/contract; floor(125/108)=1. The -$432 trade case."""
    assert risk_based_size(EQUITY, PCT, Decimal("10.8"), PV, MAXSZ) == 1


def test_very_wide_stop_floors_to_one_over_budget():
    """20pt stop -> $200/contract > $125 budget; floor=0 -> floored up to 1."""
    assert risk_based_size(EQUITY, PCT, Decimal("20"), PV, MAXSZ) == 1


def test_tight_stop_clamps_to_max_size():
    """0.1pt stop -> $1/contract; raw=125 but capped at max_size=30."""
    assert risk_based_size(EQUITY, PCT, Decimal("0.1"), PV, MAXSZ) == 30


def test_half_percent_doubles_budget():
    """0.5% = $250 budget; 3.0pt stop -> floor(250/30)=8."""
    assert risk_based_size(EQUITY, Decimal("0.5"), Decimal("3.0"), PV, MAXSZ) == 8
