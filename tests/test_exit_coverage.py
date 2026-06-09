"""Tests for the exit-coverage monitor: ExitCoverage coverage math, the broker's
exchange-order query, protective-order primitives, and config fields."""

from decimal import Decimal

from app.broker.events import ExitCoverage


def test_fully_covered_when_stop_and_target_meet_size():
    cov = ExitCoverage(
        instrument="MGC", position_size=2, side="long",
        avg_price=Decimal("2400.0"), covered_stop=2, covered_target=2,
    )
    assert cov.fully_covered is True


def test_naked_when_stop_missing():
    cov = ExitCoverage(
        instrument="MGC", position_size=2, side="long",
        avg_price=Decimal("2400.0"), covered_stop=0, covered_target=2,
    )
    assert cov.fully_covered is False


def test_naked_when_target_missing():
    cov = ExitCoverage(
        instrument="MGC", position_size=2, side="long",
        avg_price=Decimal("2400.0"), covered_stop=2, covered_target=0,
    )
    assert cov.fully_covered is False


def test_naked_when_partially_covered():
    cov = ExitCoverage(
        instrument="MGC", position_size=2, side="long",
        avg_price=Decimal("2400.0"), covered_stop=1, covered_target=2,
    )
    assert cov.fully_covered is False


def test_flat_position_is_always_covered():
    cov = ExitCoverage(
        instrument="MGC", position_size=0, side="",
        avg_price=Decimal("0"), covered_stop=0, covered_target=0,
    )
    assert cov.fully_covered is True
