"""prop_sim: per-trade combine replay from prop_rules/*.yaml (spec §6).

WHY: a strategy with positive net R can still fail most combines — the
trailing max loss and the consistency rule decide whether it is usable at
Topstep. These tests pin the rules that decide pass/fail: trailing (not
static) max loss, the 50% best-day consistency rule, the daily loss limit,
whole-contract sizing, and one fresh combine per month.

Hand-built trades: this cannot catch a runner that writes wrong net_R or
risk_usd onto trades (tests/test_research_metrics.py covers that).
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.backtest.prop_sim import PropRules, load_prop_rules, prop_sim
from app.risk.account_phase import CombineRules

REPO = Path(__file__).resolve().parents[1]


def _rules(dll=None, action="stop_day", trailing="intraday"):
    return PropRules("test", "2026-09-27", CombineRules(mll_trailing=trailing), dll, action)


def _t(day: str, usd: float, hour: int = 15, risk_usd: float = 200):
    """One trade exiting on `day` (15:00Z = morning CT) paying `usd` at 1 contract."""
    return {"exit_ts": f"{day}T{hour:02d}:00:00+00:00", "net_R": usd / risk_usd, "risk_usd": risk_usd}


def test_steady_profit_passes_and_counts_trade_days():
    r = prop_sim([_t("2026-01-05", 1000), _t("2026-01-06", 1000), _t("2026-01-07", 1000)], _rules())
    assert (r["passed"], r["n_combines"], r["median_trade_days_to_pass"]) == (1, 1, 3)
    assert r["pass_rate"] == 1.0


def test_one_big_day_is_blocked_by_consistency_rule():
    r = prop_sim([_t("2026-01-05", 2500), _t("2026-01-06", 500)], _rules())
    assert r["passed"] == 0
    assert r["unresolved_consistency_blocked"] == 1


def test_max_loss_trails_the_high_not_the_start():
    """+1500 then -2000: balance 49,500 is above a static 48,000 floor but at
    the trailed floor 51,500 - 2,000 — the combine is failed."""
    r = prop_sim([_t("2026-01-05", 1500), _t("2026-01-06", -1000), _t("2026-01-07", -1000)], _rules())
    assert r["failed_max_loss"] == 1


def test_daily_loss_limit_stops_the_day():
    """After -1000 the +2000 later that day never happens, so day 2's -1000
    reaches the 48,000 floor. Counting the +2000 would leave it alive."""
    trades = [_t("2026-01-05", -1000, 14), _t("2026-01-05", 2000, 16), _t("2026-01-06", -1000)]
    assert prop_sim(trades, _rules(dll=Decimal("1000")))["failed_max_loss"] == 1
    assert prop_sim(trades, _rules(dll=None))["failed_max_loss"] == 0


def test_daily_loss_limit_fail_action_fails_the_combine():
    r = prop_sim([_t("2026-01-05", -1000)], _rules(dll=Decimal("1000"), action="fail"))
    assert r["failed_daily_loss"] == 1


def test_new_combine_each_month_ignores_earlier_trades():
    trades = [_t("2026-01-05", -1500),
              _t("2026-02-02", 1000), _t("2026-02-03", 1000), _t("2026-02-04", 1000)]
    r = prop_sim(trades, _rules())
    assert r["n_combines"] == 2
    assert (r["passed"], r["unresolved"]) == (1, 1)   # Jan: +1500 net, never reaches target
    assert r["pass_rate"] == 1.0                       # unresolved combines excluded


def test_sizing_floors_to_whole_contracts():
    """$150 risk per contract at a $200 budget → 1 contract, not 1.33.
    $250 per contract → can't take the trade at all."""
    three_days = [_t(d, 900, risk_usd=150) for d in ("2026-01-05", "2026-01-06", "2026-01-07")]
    r = prop_sim(three_days, _rules(), Decimal("200"))
    assert r["passed"] == 0          # 3 × $900 = $2,700; fractional sizing would make $3,600
    r = prop_sim([_t("2026-01-05", 500, risk_usd=250)], _rules(), Decimal("200"))
    assert r["trades_skipped_risk_too_wide"] == 1 and r["n_combines"] == 0


def test_checked_in_rules_load_and_warn_until_verified():
    rules = load_prop_rules("topstep_50k", REPO / "prop_rules")
    assert rules.combine.profit_target == Decimal("3000")
    assert rules.combine.mll_distance == Decimal("2000")
    r = prop_sim([], rules)
    if rules.last_checked is None:
        assert "never been checked" in r["warning"]


def test_non_topstep_consistency_fraction_is_refused(tmp_path):
    src = (REPO / "prop_rules" / "topstep_50k.yaml").read_text()
    (tmp_path / "x.yaml").write_text(src.replace("consistency_best_day_frac: 0.5",
                                                 "consistency_best_day_frac: 0.4"))
    with pytest.raises(ValueError, match="consistency"):
        load_prop_rules("x", tmp_path)


def test_consumes_the_fields_research_metrics_writes():
    """prop_sim reads net_R/risk_usd off the trade dicts research_metrics mutates;
    a renamed key would otherwise surface only as an error in a live run."""
    from app.backtest.costs import cost_spec
    from app.backtest.metrics import research_metrics
    trades = [{"side": "long", "entry_price": "2000.1", "exit_price": "2030", "stop_dist": "10",
               "exit_is_stop": False, "entry_ts": f"{d}T14:00:00+00:00",
               "exit_ts": f"{d}T15:00:00+00:00"}
              for d in ("2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08")]
    research_metrics(trades, cost_spec("MGC"), 1)
    r = prop_sim(trades, _rules(), Decimal("200"))
    # $100 risk per MGC contract → 2 contracts × ~+3R × $100 ≈ $590/day: not yet $3,000
    assert r["trades_skipped_risk_too_wide"] == 0
    assert (r["n_combines"], r["unresolved"]) == (1, 1)
