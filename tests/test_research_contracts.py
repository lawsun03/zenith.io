from __future__ import annotations

from datetime import date

from research.data.contracts import ContractMonth, contract_months_covering
from research.data.instruments import INSTRUMENTS


def test_raw_symbol_spelling():
    """Single-digit year — verified against the live Databento API (GLBX.MDP3
    raw_symbol resolution rejects "GCZ24" and accepts "GCZ4")."""
    m = ContractMonth("GC", "Z", 2024)
    assert m.raw_symbol == "GCZ4"


def test_gc_months_covering_a_short_window_include_the_active_front_month():
    spec = INSTRUMENTS["GC"]
    months = contract_months_covering(spec, date(2024, 6, 15), date(2024, 6, 20))
    symbols = {m.raw_symbol for m in months}
    # June is itself a listed GC month (M); the prior listed month (Apr, J)
    # must also be present since it could still be the front contract early
    # in June.
    assert "GCJ4" in symbols
    assert "GCM4" in symbols


def test_es_only_lists_quarterly_months():
    spec = INSTRUMENTS["ES"]
    months = contract_months_covering(spec, date(2023, 1, 1), date(2023, 12, 31))
    codes = {m.month_code for m in months}
    assert codes <= {"H", "M", "U", "Z"}


def test_months_are_sorted_chronologically():
    spec = INSTRUMENTS["GC"]
    months = contract_months_covering(spec, date(2020, 1, 1), date(2021, 12, 31))
    keys = [m.sort_key for m in months]
    assert keys == sorted(keys)
