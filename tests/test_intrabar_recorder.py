"""Tests for the intrabar forming-bar snapshot recorder."""
from datetime import datetime, timezone
from decimal import Decimal

from app.sim.events import Bar
from app.sim.topstepx import (
    _INTRABAR_HEADERS,
    _append_intrabar_csv,
    _intrabar_row,
)


def _bar():
    return Bar(
        instrument="MGC",
        timeframe="1min",
        ts=datetime(2026, 5, 26, 13, 4, tzinfo=timezone.utc),
        open=Decimal("4530.0"),
        high=Decimal("4531.5"),
        low=Decimal("4529.5"),
        close=Decimal("4530.8"),
        volume=42,
    )


def _sample_ts():
    return datetime(2026, 5, 26, 13, 4, 35, tzinfo=timezone.utc)


def test_intrabar_row_fields():
    """Row is built in header order, prices as plain strings, full UTC timestamps."""
    row = _intrabar_row(_bar(), _sample_ts())
    assert row == [
        "2026-05-26T13:04:35+00:00",  # sample_ts
        "MGC",                        # instrument
        "2026-05-26T13:04:00+00:00",  # bar_minute (forming bar ts)
        "4530.0",                     # open
        "4531.5",                     # high
        "4529.5",                     # low
        "4530.8",                     # close
        "42",                         # volume
    ]
    assert len(row) == len(_INTRABAR_HEADERS)


def test_append_creates_file_with_header(tmp_path):
    """First append creates the file and writes the header once, then the data row."""
    path = tmp_path / "intrabar_MGC.csv"
    _append_intrabar_csv(_bar(), _sample_ts(), path=path)
    lines = path.read_text().splitlines()
    assert lines[0] == ",".join(_INTRABAR_HEADERS)
    assert len(lines) == 2  # header + one data row
    assert lines[1].startswith("2026-05-26T13:04:35+00:00,MGC,")


def test_append_writes_header_only_once(tmp_path):
    """Appending twice yields one header and two data rows (rolling-file contract)."""
    path = tmp_path / "intrabar_MGC.csv"
    _append_intrabar_csv(_bar(), _sample_ts(), path=path)
    _append_intrabar_csv(_bar(), _sample_ts(), path=path)
    lines = path.read_text().splitlines()
    header_count = sum(1 for ln in lines if ln == ",".join(_INTRABAR_HEADERS))
    assert header_count == 1
    assert len(lines) == 3  # header + two data rows
