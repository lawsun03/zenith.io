"""Research metrics: gross vs net vs stressed-net R (protocol §6).

WHY: in the SI/GC study most edges of +0.05–0.15R gross died once ~2 ticks of
slippage plus commission were charged against small stops. These tests pin
that the cost layer (1) agrees to the cent with the broker that produced the
fills, (2) scales with stop size exactly as it should, and (3) flags
cost-dominated strategies.

The broker-consistency test drives PaperBroker with real fills; the rest use
hand-built trade dicts and cannot catch a runner that mislabels exit types.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.backtest.costs import CostSpec, cost_spec
from app.backtest.metrics import research_metrics, trade_r
from app.backtest.runner import _merge_excursion, _reconstruct_trades
from app.broker.events import Bar, Fill
from app.broker.paper import PaperBroker

MGC = CostSpec(tick=Decimal("0.10"), tick_value=Decimal("1"), commission_rt=Decimal("1.48"))


def _trade(side="long", entry="100.1", exit_="102", stop_dist="1", exit_is_stop=False,
           ts="2026-01-06T15:00:00+00:00"):
    return {"side": side, "entry_price": entry, "exit_price": exit_, "stop_dist": stop_dist,
            "exit_is_stop": exit_is_stop, "entry_ts": ts}


def test_unknown_instrument_fails_loud():
    with pytest.raises(KeyError):
        cost_spec("ZZZ")


def test_silver_cost_spec_uses_broker_tables():
    s = cost_spec("SIL")
    assert s.tick == Decimal("0.005") and s.point_value == Decimal("1000")


def test_target_exit_gross_removes_entry_slippage_only():
    # Market entry slipped 1 tick (100.0 → 100.1); limit target at 102 is unslipped.
    r = trade_r(_trade(exit_="102"), MGC, applied_slip_ticks=1)
    assert r["gross_R"] == pytest.approx(2.0)                 # 102 − 100.0 over 1pt risk
    # cost = 1 tick entry (0.1) + 0 limit exit + 1.48/10 commission = 0.248 pts
    assert r["cost_R"] == pytest.approx(0.248)
    assert r["net_R"] == pytest.approx(2.0 - 0.248)


def test_stop_exit_gross_removes_both_slips():
    # Long stop at 99.0 filled 98.9 after slippage.
    r = trade_r(_trade(exit_="98.9", exit_is_stop=True), MGC, applied_slip_ticks=1)
    assert r["gross_R"] == pytest.approx(-1.0)
    assert r["cost_R"] == pytest.approx(0.348)                # 2 ticks + commission


def test_stress_doubles_slippage_not_commission():
    r = trade_r(_trade(exit_="98.9", exit_is_stop=True), MGC, applied_slip_ticks=1, stress_multiplier=2.0)
    assert r["gross_R"] - r["net_R_stress"] == pytest.approx(0.4 + 0.148)


def test_small_stop_turns_gross_edge_negative():
    # Same +0.1R gross edge; a 5-tick stop is eaten by costs, a 50-tick one survives.
    small = trade_r(_trade(entry="100.1", exit_="100.05", stop_dist="0.5"), MGC, 1)
    big = trade_r(_trade(entry="100.1", exit_="100.5", stop_dist="5"), MGC, 1)
    assert small["gross_R"] == pytest.approx(0.1) and big["gross_R"] == pytest.approx(0.1)
    assert small["net_R"] < 0 < big["net_R"]


async def test_net_R_matches_broker_realized_pnl_to_the_cent():
    # One costing path: the metrics layer must reproduce the broker's own P&L
    # when the CostSpec matches the broker's slippage and commission.
    broker = PaperBroker(slippage_ticks_market=1, commission_per_side=Decimal("0.74"))
    fills: list[dict] = []

    async def collect(f: Fill):
        fills.append({"ts": f.ts.isoformat(), "instrument": f.instrument, "side": f.side,
                      "fill_price": str(f.fill_price), "size": f.size, "is_entry": f.is_entry,
                      "is_stop": f.is_stop, "realized_pnl_delta": str(f.realized_pnl_delta),
                      "order_id": f.broker_order_id or ""})

    broker.on_fill(collect)
    await broker.connect()
    t0 = datetime(2026, 1, 6, 15, 0, tzinfo=timezone.utc)

    def bar(i, o, h, l, c):
        return Bar(instrument="MGC", timeframe="1min", ts=t0 + timedelta(minutes=i),
                   open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=1)

    await broker.inject_bar(bar(0, "100", "100", "100", "100"))
    await broker.place_bracket("MGC", "long", 1, Decimal("100"), Decimal("99"), Decimal("102"))
    await broker.inject_bar(bar(1, "100", "100.2", "98.5", "99"))   # stopped
    trades = _reconstruct_trades(fills)
    _merge_excursion(trades[0], broker.excursions_by_order_id()["PAPER-1"])
    broker_pnl = sum(Decimal(f["realized_pnl_delta"]) for f in fills)

    r = trade_r(trades[0], MGC, applied_slip_ticks=1)
    assert Decimal(str(r["net_R"])) * Decimal(str(r["risk_usd"])) == pytest.approx(broker_pnl)
    assert trades[0]["exit_is_stop"] is True


def test_summary_metrics():
    trades = [
        _trade(exit_="102", ts="2024-03-01T15:00:00+00:00"),                     # +2R gross
        _trade(exit_="98.9", exit_is_stop=True, ts="2024-03-02T15:00:00+00:00"),  # −1R
        _trade(exit_="98.9", exit_is_stop=True, ts="2025-03-03T15:00:00+00:00"),  # −1R
        _trade(exit_="98.9", exit_is_stop=True, ts="2025-03-04T15:00:00+00:00"),  # −1R
    ]
    m = research_metrics(trades, MGC, applied_slip_ticks=1)
    assert m["n_trades"] == 4
    assert m["gross_R_mean"] == pytest.approx(-0.25)
    assert m["net_R_mean"] < m["gross_R_mean"]
    assert m["net_R_mean_stress"] < m["net_R_mean"]
    assert m["cost_R_median"] == pytest.approx(0.348)
    assert m["cost_R_flag"] is True
    assert m["longest_losing_streak"] == 3
    assert m["per_year"][2024]["n"] == 2 and m["per_year"][2025]["n"] == 2
    assert m["years_positive_frac"] == 0.5
    # Dropping 2024 (best year) leaves only the two 2025 losers.
    assert m["drop_best_year_net_R"] == pytest.approx(-1.348)
    assert m["max_drawdown_R"] == pytest.approx(1.348 * 3)
    assert m["median_risk_usd"] == 10.0
    assert "net_R" in trades[0], "per-trade R must be written back onto the trade list"


def test_trades_without_stop_are_counted_not_hidden():
    m = research_metrics([_trade(stop_dist="0")], MGC, applied_slip_ticks=1)
    assert m["n_trades"] == 0 and m["excluded_no_stop"] == 1
