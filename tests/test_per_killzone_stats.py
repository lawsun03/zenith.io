"""Tests for per-killzone stat tracking in backtest runner."""
from decimal import Decimal

from app.backtest.runner import _compute_stats
from app.risk.config import fifty_k_combine
from app.risk.state import RiskState


def _risk():
    return RiskState(config=fifty_k_combine())


def _fill(i, is_entry, pnl, kz="london"):
    return {
        "ts": f"2026-01-02T{9 + i:02d}:00:00+00:00",
        "instrument": "MGC",
        "side": "long",
        "fill_price": "100",
        "size": 1,
        "is_entry": is_entry,
        "realized_pnl_delta": str(pnl),
        "killzone": kz,
    }


def test_by_killzone_two_wins_one_loss():
    """london: 1W 1L. ny_am: 1W. by_killzone correctly reflects this."""
    fills = [
        _fill(0, True,   "-0.74", "london"),
        _fill(1, False,  "100",   "london"),
        _fill(2, True,   "-0.74", "london"),
        _fill(3, False,  "-50",   "london"),
        _fill(4, True,   "-0.74", "ny_am"),
        _fill(5, False,  "200",   "ny_am"),
    ]
    stats = _compute_stats(fills, _risk(), Decimal("50000"))

    assert set(stats.by_killzone) == {"london", "ny_am"}

    ld = stats.by_killzone["london"]
    assert ld["trades"] == 2
    assert ld["wins"] == 1
    assert ld["losses"] == 1
    assert ld["win_rate"] == 50.0
    assert Decimal(str(ld["net_pnl"])) == Decimal("50")  # 100 + (-50)

    ny = stats.by_killzone["ny_am"]
    assert ny["trades"] == 1
    assert ny["wins"] == 1
    assert ny["losses"] == 0
    assert ny["win_rate"] == 100.0


def test_by_killzone_missing_field_defaults_to_unknown():
    """Fills without 'killzone' key fall under 'unknown'."""
    fills = [
        {"ts": "2026-01-02T09:00:00+00:00", "instrument": "MGC", "side": "long",
         "fill_price": "100", "size": 1, "is_entry": True, "realized_pnl_delta": "-0.74"},
        {"ts": "2026-01-02T09:30:00+00:00", "instrument": "MGC", "side": "short",
         "fill_price": "102", "size": 1, "is_entry": False, "realized_pnl_delta": "100"},
    ]
    stats = _compute_stats(fills, _risk(), Decimal("50000"))
    assert "unknown" in stats.by_killzone
    assert stats.by_killzone["unknown"]["trades"] == 1


def test_by_killzone_empty_when_no_exits():
    """No exit fills → by_killzone is empty."""
    fills = [
        _fill(0, True, "-0.74", "london"),
    ]
    stats = _compute_stats(fills, _risk(), Decimal("50000"))
    assert stats.by_killzone == {}
