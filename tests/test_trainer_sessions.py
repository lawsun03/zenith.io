"""TrainerSession state and build_queue orchestration tests.

build_queue is tested against a monkeypatched research.trainer.sessions.
load_bars (a Polars frame, not the real Databento-backed Parquet corpus)
so this suite runs without any real market data on disk — the contract
being tested is the wiring (data -> decision points -> filter -> sample),
not load_bars itself (research/data/ has its own tests for that).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import polars as pl
import pytest

from research.data.loader import HoldoutAccessError
from research.gates.ensemble import build_ensemble
from research.trainer.scoring import Answer
from research.trainer import sessions as sessions_mod
from research.trainer.sampling import Candidate
from research.trainer.sessions import DecisionRecord, TrainerSession, build_queue, start_session

BASE_TS = datetime(2026, 1, 6, 13, 30, tzinfo=timezone.utc)  # 08:30 ET

_IFVG_DOC = {
    "ir_version": "1.0", "name": "trainer session fixture",
    "instruments": ["NQ", "ES", "GC"],
    "session": {"start": "08:30", "end": "11:00", "tz": "America/New_York"},
    "entry": {"op": "or", "operands": [
        {"op": "and", "operands": [
            {"op": "sweep_of", "level": "swing_low", "n": 2,
             "min_offset": 0.20, "within_bars": 5, "recognizable": True},
            {"op": "displacement", "direction": "up", "min_atr": 1.0, "recognizable": True},
            {"op": "fvg", "direction": "up", "recognizable": True},
        ]},
        {"op": "and", "operands": [
            {"op": "sweep_of", "level": "swing_high", "n": 2,
             "min_offset": 0.20, "within_bars": 5, "recognizable": True},
            {"op": "displacement", "direction": "down", "min_atr": 1.0, "recognizable": True},
            {"op": "fvg", "direction": "down", "recognizable": True},
        ]},
    ]},
    "exit": {"op": "session_end", "recognizable": False},
    "stop": {"type": "structural", "anchor": "sweep_extreme", "multiple": 1.0,
             "lookback": 14, "buffer": 0.30},
    "target": {"type": "r_multiple", "multiple": 2.5},
    "sizing": {"family": "micro", "vol_target_annual": 0.15, "max_contracts": 10},
}


def _fixture_frame() -> pl.DataFrame:
    """Same known-good iFVG long fixture used in tests/test_ir_engine.py,
    reshaped into the Float64-OHLC/UInt64-volume frame research/data/loader.py
    hands back."""
    rows = [(i, 100.0, 100.5, 99.5, 100.0) for i in range(14)]
    rows += [
        (14, 99.0, 99.2, 98.8, 99.0),
        (15, 98.8, 98.9, 97.5, 97.8),
        (16, 97.7, 97.8, 97.0, 97.3),
        (17, 97.3, 97.5, 97.1, 97.2),
        (18, 97.2, 97.3, 96.5, 97.0),
        (19, 97.0, 97.4, 96.8, 97.2),
        (20, 97.2, 97.6, 96.9, 97.4),
        (21, 97.4, 97.5, 95.5, 97.3),
        (22, 97.3, 99.5, 97.2, 99.3),
        (23, 99.3, 99.6, 99.1, 99.4),
        (24, 99.4, 108.0, 99.3, 107.5),
    ]
    return pl.DataFrame({
        "ts": [BASE_TS + timedelta(minutes=i) for i, *_ in rows],
        "open": [o for _, o, h, l, c in rows],
        "high": [h for _, o, h, l, c in rows],
        "low": [l for _, o, h, l, c in rows],
        "close": [c for _, o, h, l, c in rows],
        "volume": [100] * len(rows),
    })


def _make_ensemble():
    return build_ensemble("test-family", [("hyp-1", _IFVG_DOC)])


# --- TrainerSession -------------------------------------------------------

def _candidate(fired=True):
    from research.ir.engine import DecisionPoint
    point = DecisionPoint(
        ts=BASE_TS, fired=fired, near_miss=False,
        side="long" if fired else None,
        entry_price=Decimal("100") if fired else None,
        stop_price=Decimal("99") if fired else None,
        target_price=Decimal("102") if fired else None,
    )
    return Candidate(hypothesis_id="hyp-1", instrument="NQ", regime_label=None, point=point)


def test_session_cursor_advances_and_reports_done():
    session = start_session("ens-1", [_candidate(), _candidate(fired=False)])
    assert not session.done
    session.answer_current(Answer(is_setup=True, direction="long", stop_price=Decimal("99")))
    assert not session.done
    session.answer_current(Answer(is_setup=False))
    assert session.done
    assert session.current() is None


def test_answering_past_the_end_raises():
    session = start_session("ens-1", [_candidate()])
    session.answer_current(Answer(is_setup=True, direction="long", stop_price=Decimal("99")))
    with pytest.raises(ValueError):
        session.answer_current(Answer(is_setup=False))


def test_tally_matches_manual_scoring():
    session = start_session("ens-1", [_candidate(), _candidate(fired=False)])
    session.answer_current(Answer(is_setup=True, direction="long", stop_price=Decimal("99")))
    session.answer_current(Answer(is_setup=True, direction="short", stop_price=Decimal("1")))
    tally = session.tally()
    assert tally.n_decisions == 2
    assert tally.setups_correctly_taken == 1
    assert tally.false_positives == 1


# --- build_queue -----------------------------------------------------------

def test_build_queue_extracts_a_fired_candidate_from_loaded_bars(monkeypatch):
    monkeypatch.setattr(sessions_mod, "load_bars", lambda *a, **kw: _fixture_frame())
    ensemble = _make_ensemble()
    queue = build_queue(
        ensemble, start=date(2026, 1, 6), end=date(2026, 1, 6),
        n_decisions=50, regime_label=None, previously_wrong=set(),
        rng=__import__("random").Random(1),
    )
    assert any(c.point.fired for c in queue)


def test_build_queue_regime_filter_excludes_unmatched_days(monkeypatch):
    monkeypatch.setattr(sessions_mod, "load_bars", lambda *a, **kw: _fixture_frame())
    monkeypatch.setattr(sessions_mod, "session_dates_for_label", lambda instrument, label: set())
    ensemble = _make_ensemble()
    queue = build_queue(
        ensemble, start=date(2026, 1, 6), end=date(2026, 1, 6),
        n_decisions=50, regime_label="CPI", previously_wrong=set(),
        rng=__import__("random").Random(1),
    )
    assert queue == []


def test_build_queue_propagates_holdout_access_error(monkeypatch):
    def _raise(*a, **kw):
        raise HoldoutAccessError("holdout")
    monkeypatch.setattr(sessions_mod, "load_bars", _raise)
    ensemble = _make_ensemble()
    with pytest.raises(HoldoutAccessError):
        build_queue(
            ensemble, start=date(2026, 1, 6), end=date(2026, 1, 6),
            n_decisions=10, regime_label=None, previously_wrong=set(),
            rng=__import__("random").Random(1),
        )
