"""Full tests for canonical IR hashing (item 2).

Gate 0 (docs/research-loop/gates.md) rejects a candidate whose ir_hash is
already in the ledger — the whole point is to stop the loop from re-testing
(and re-spending trial budget on, CLAUDE.md rule 7) an idea it already
tried, even if that idea comes back serialized with different key order.
"""
from __future__ import annotations

from typing import Any

from hypothesis import given, strategies as st

from research.stats.ir_hash import canonical_json, ir_hash


def _shuffle_keys(obj: Any) -> Any:
    """Rebuild `obj` recursively with every dict's keys reinserted in
    reverse order. List order and all values are untouched."""
    if isinstance(obj, dict):
        return {k: _shuffle_keys(v) for k, v in reversed(list(obj.items()))}
    if isinstance(obj, list):
        return [_shuffle_keys(v) for v in obj]
    return obj


_json_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-10_000, max_value=10_000),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(max_size=20),
)

json_value = st.recursive(
    _json_scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(max_size=10), children, max_size=5),
    ),
    max_leaves=30,
)


def test_ir_hash_is_deterministic():
    doc = {"a": 1, "b": [1, 2, 3]}
    assert ir_hash(doc) == ir_hash(doc)


def test_ir_hash_is_a_sha256_hex_digest():
    digest = ir_hash({"x": 1})
    assert len(digest) == 64
    int(digest, 16)  # raises ValueError if not valid hex


def test_reordered_top_level_keys_hash_identically():
    a = {"name": "orb", "instruments": ["NQ", "ES", "GC"], "ir_version": "1.0"}
    b = {"ir_version": "1.0", "instruments": ["NQ", "ES", "GC"], "name": "orb"}
    assert ir_hash(a) == ir_hash(b)


def test_reordered_nested_keys_hash_identically():
    a = {"entry": {"op": "and", "predicates": [{"kind": "sweep", "side": "high"}]}}
    b = {"entry": {"predicates": [{"side": "high", "kind": "sweep"}], "op": "and"}}
    assert ir_hash(a) == ir_hash(b)


def test_list_element_order_is_significant():
    """Unlike dict keys, list order is semantic (e.g. an ordered chain of
    predicates) — canonicalization must never reorder it."""
    a = {"predicates": ["a", "b"]}
    b = {"predicates": ["b", "a"]}
    assert ir_hash(a) != ir_hash(b)


def test_different_values_hash_differently():
    assert ir_hash({"x": 1}) != ir_hash({"x": 2})


def test_extra_key_changes_the_hash():
    assert ir_hash({"x": 1}) != ir_hash({"x": 1, "y": 2})


def test_canonical_json_is_compact_and_key_sorted():
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


@given(json_value)
def test_hash_stable_under_recursive_key_reordering(value):
    """CLAUDE.md rule 9 / gate 0: two IR documents differing only in key
    order, at any nesting depth, must hash identically."""
    assert ir_hash(value) == ir_hash(_shuffle_keys(value))
