"""Stage d — variant enumeration (research/loop/variants.py)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from research.loop.providers import ChatResponse
from research.loop.variants import VariantParseError, enumerate_variants

_STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "research" / "ir" / "strategies"
GOOD_IR = json.loads((_STRATEGIES_DIR / "ifvg_sweep.json").read_text())


def _variant(multiple: float) -> dict:
    v = copy.deepcopy(GOOD_IR)
    v["target"]["multiple"] = multiple
    return v


def _fake_chat(param_grid: dict, variants: list[dict], model_version="kimi-k3-2026-08-01"):
    def _fn(prompt: str) -> ChatResponse:
        return ChatResponse(
            text=json.dumps({"param_grid": param_grid, "variants": variants}), model_version=model_version,
        )
    return _fn


def test_all_valid_variants_are_kept():
    variants = [_variant(2.0), _variant(2.5), _variant(3.0)]
    result = enumerate_variants(
        _fake_chat({"target.multiple": [2.0, 2.5, 3.0]}, variants), mechanism="m", base_ir=GOOD_IR,
    )
    assert result.n_variants_swept == 3
    assert result.param_grid == {"target.multiple": [2.0, 2.5, 3.0]}
    assert result.model_version == "kimi-k3-2026-08-01"


def test_invalid_variants_are_dropped_not_coerced():
    bad = copy.deepcopy(GOOD_IR)
    bad["instruments"] = ["NQ", "ES"]  # violates pooling — must be dropped, never fixed silently
    variants = [_variant(2.0), bad]
    result = enumerate_variants(
        _fake_chat({"target.multiple": [2.0]}, variants), mechanism="m", base_ir=GOOD_IR,
    )
    assert result.n_variants_swept == 1
    assert all(v["instruments"] == ["NQ", "ES", "GC"] for v in result.variant_irs)


def test_no_valid_variants_falls_back_to_base_ir():
    bad = copy.deepcopy(GOOD_IR)
    bad["instruments"] = ["NQ"]
    result = enumerate_variants(
        _fake_chat({}, [bad]), mechanism="m", base_ir=GOOD_IR,
    )
    assert result.n_variants_swept == 1
    assert result.variant_irs == (GOOD_IR,)


def test_variants_truncated_to_max_variants():
    variants = [_variant(2.0 + i * 0.1) for i in range(20)]
    result = enumerate_variants(
        _fake_chat({}, variants), mechanism="m", base_ir=GOOD_IR, max_variants=5,
    )
    assert result.n_variants_swept == 5


def test_unparsable_response_raises_variant_parse_error():
    def _fn(prompt):
        return ChatResponse(text="not json", model_version="v1")

    with pytest.raises(VariantParseError):
        enumerate_variants(_fn, mechanism="m", base_ir=GOOD_IR)
