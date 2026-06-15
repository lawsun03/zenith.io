"""Defining-behavior tests for the B94 event-calendar router primitives.

These pin the day-type routing semantics that the funded-pipeline attribution
depends on:
  * additive overlays add R*R_FIXED dollars ON the event day (multi-instrument
    P&L merged in R-space, sized to a fixed-fractional dollar risk);
  * stacking two disjoint-date overlays == summing each overlay's injection
    (the "router toggled by day type" reduces to per-date injection because
    CPI and FOMC dates never collide);
  * "nofill"/"nodata" events inject nothing (no phantom P&L on a no-trade day);
  * the CPI mode-switch REPLACES the base day's P&L (it must not double-count
    base + straddle on a mode-switch day).

If any of these break, the B94 pipeline attribution silently mis-states which
piece (CPI vs gold-FOMC) added value -- the whole point of the item.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from scripts.run_b94_pipeline import mode_switch_cpi, stack_overlays
from scripts.b93_cpi_overlay import R_FIXED


def _day(y, m, d, pnl):
    return (datetime(y, m, d, 16, 0, tzinfo=timezone.utc), Decimal(str(pnl)))


def _base():
    # three consecutive trade days with known base P&L
    return [_day(2024, 1, 10, "100"), _day(2024, 1, 11, "-50"), _day(2024, 1, 12, "200")]


def test_additive_overlay_adds_r_times_rfixed_on_event_day():
    base = _base()
    cpi = [(date(2024, 1, 11), "tp", 2.0)]          # +2R on the middle day
    out, standalone = stack_overlays(base, cpi)
    assert standalone == 0
    by_date = {ts.date(): eq for ts, eq in out}
    # event day = base(-50) + 2R*R_FIXED ; other days untouched
    assert by_date[date(2024, 1, 11)] == Decimal("-50") + Decimal("2") * R_FIXED
    assert by_date[date(2024, 1, 10)] == Decimal("100")
    assert by_date[date(2024, 1, 12)] == Decimal("200")


def test_nofill_and_nodata_inject_nothing():
    base = _base()
    out, standalone = stack_overlays(base, [(date(2024, 1, 11), "nofill", 0.0),
                                            (date(2024, 1, 12), "nodata", 0.0)])
    assert standalone == 0
    assert {ts.date(): eq for ts, eq in out} == {ts.date(): eq for ts, eq in base}


def test_stacking_disjoint_overlays_equals_sum_of_each():
    base = _base()
    cpi = [(date(2024, 1, 10), "tp", 3.0)]          # CPI day
    fomc = [(date(2024, 1, 12), "stop", -1.0)]      # FOMC day (disjoint)
    stacked, _ = stack_overlays(base, cpi, fomc)
    by_date = {ts.date(): eq for ts, eq in stacked}
    assert by_date[date(2024, 1, 10)] == Decimal("100") + Decimal("3") * R_FIXED
    assert by_date[date(2024, 1, 12)] == Decimal("200") + Decimal("-1") * R_FIXED
    assert by_date[date(2024, 1, 11)] == Decimal("-50")  # untouched
    # order-independence on disjoint dates
    stacked_rev, _ = stack_overlays(base, fomc, cpi)
    assert {ts.date(): eq for ts, eq in stacked_rev} == by_date


def test_standalone_event_appends_a_new_day():
    base = _base()
    cpi = [(date(2024, 2, 1), "tp", 1.0)]           # date not in base
    out, standalone = stack_overlays(base, cpi)
    assert standalone == 1
    by_date = {ts.date(): eq for ts, eq in out}
    assert by_date[date(2024, 2, 1)] == Decimal("1") * R_FIXED
    assert len(out) == len(base) + 1


def test_mode_switch_replaces_base_pnl_not_adds():
    base = _base()
    cpi = [(date(2024, 1, 11), "tp", 2.0)]
    out = mode_switch_cpi(base, cpi)
    by_date = {ts.date(): eq for ts, eq in out}
    # middle day base P&L (-50) is DISCARDED; only the straddle P&L remains
    assert by_date[date(2024, 1, 11)] == Decimal("2") * R_FIXED
    # non-CPI days keep their base P&L
    assert by_date[date(2024, 1, 10)] == Decimal("100")
    assert by_date[date(2024, 1, 12)] == Decimal("200")
