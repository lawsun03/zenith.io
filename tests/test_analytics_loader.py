from pathlib import Path

import pytest

from app.analytics.loader import (
    _parse_log_line,
    _parse_summary_file,
    load_all_trades,
    load_backtest_summaries,
    load_config,
    load_log_events,
)


def test_load_all_trades_returns_list():
    result = load_all_trades()
    assert isinstance(result, list)


def test_trade_has_required_fields():
    trades = load_all_trades()
    if not trades:
        pytest.skip("no trade files on disk")
    for field in ("ts", "instrument", "side", "type", "fill_price", "size", "realized_pnl"):
        assert field in trades[0]


def test_old_trade_file_missing_cols_handled(tmp_path, monkeypatch):
    """trades.csv (no killzone/stop/target) must not raise and must include key."""
    csv_content = (
        "ts,instrument,side,type,fill_price,size,realized_pnl,broker_order_id\n"
        "2026-01-01T00:00:00+00:00,MES,long,EXIT,5000.0,1,25.0,abc123\n"
    )
    (tmp_path / "trades").mkdir()
    (tmp_path / "trades" / "trades_old.csv").write_text(csv_content, encoding="utf-8")
    import app.analytics.loader as loader_mod
    monkeypatch.setattr(loader_mod, "_ROOT", tmp_path)
    trades = loader_mod.load_all_trades()
    assert len(trades) == 1
    assert "killzone" in trades[0]
    assert trades[0]["killzone"] is None
    assert trades[0]["stop"] is None


def test_parse_summary_file(tmp_path: Path):
    text = """\
============================================================
BACKTEST SUMMARY: body_atr_multiple=1.0  swing_lookback=2  r_multiple=1.5
============================================================
Verdict:           PROFITABLE

Net P&L:           +$1,706.00
  Gross profit:    +$3,939.00
  Gross loss:      -$2,233.00

Total trades:      103
  Winners:         57  (55.3%)
  Losers:          46

Profit factor:   1.76
Expectancy:      +$16.56 per trade
Max drawdown:    -$321.00

By killzone:
  London      trades= 47  win_rate= 63.8%  net=    +$1,000.00
  NY AM       trades= 35  win_rate= 48.6%  net=      +$544.00

============================================================
"""
    p = tmp_path / "summary_run00.txt"
    p.write_text(text, encoding="utf-8")
    result = _parse_summary_file(p)
    assert result is not None
    assert result["net_pnl"] == pytest.approx(1706.0)
    assert result["total_trades"] == 103
    assert result["win_rate"] == pytest.approx(0.553, abs=0.001)
    assert result["params"]["r_multiple"] == "1.5"
    assert "london" in result["killzones"]
    assert result["killzones"]["london"]["trades"] == 47
    assert result["killzones"]["london"]["net_pnl"] == pytest.approx(1000.0)


def test_parse_log_line_valid():
    line = "08:25:01 INFO    app.execution.engine | Signal fired: short"
    r = _parse_log_line(line)
    assert r is not None
    assert r["level"] == "INFO"
    assert r["logger"] == "app.execution.engine"
    assert "Signal fired" in r["message"]


def test_parse_log_line_invalid():
    assert _parse_log_line("garbage with no structure") is None


def test_load_config_returns_dict():
    assert isinstance(load_config(), dict)


def test_load_backtest_summaries_returns_list():
    summaries = load_backtest_summaries()
    assert isinstance(summaries, list)
    if summaries:
        assert "net_pnl" in summaries[0]
        assert "params" in summaries[0]
