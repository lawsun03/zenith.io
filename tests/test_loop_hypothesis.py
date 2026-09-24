"""Stage b — hypothesis drafting (research/loop/hypothesis.py)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from research.loop.hypothesis import (
    HypothesisInvalidIRError,
    HypothesisParseError,
    generate_hypothesis,
)
from research.loop.providers import ChatResponse
from research.loop.triage import TriagedAnomaly

_STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "research" / "ir" / "strategies"
GOOD_IR = json.loads((_STRATEGIES_DIR / "ifvg_sweep.json").read_text())

ANOMALY = TriagedAnomaly(
    anomaly_id="NQ:2024-01-02", instrument="NQ", session_date="2024-01-02",
    regime_label="scheduled_macro", regime_label_confidence=0.9, rationale="CPI print",
)


def _fake_chat(mechanism: str, ir: dict, model_version="astra-2026-08-01"):
    def _fn(prompt: str) -> ChatResponse:
        return ChatResponse(text=json.dumps({"mechanism": mechanism, "ir": ir}), model_version=model_version)
    return _fn


def test_valid_response_produces_a_draft():
    draft = generate_hypothesis(
        _fake_chat("liquidity is swept then reverses", GOOD_IR), ANOMALY,
        model_name="gpt-6-astra", temperature=0.7,
    )
    assert draft.mechanism == "liquidity is swept then reverses"
    assert draft.ir == GOOD_IR
    assert draft.model_name == "gpt-6-astra"
    assert draft.model_version == "astra-2026-08-01"
    assert draft.temperature == 0.7
    assert len(draft.prompt_hash) == 64


def test_invalid_ir_is_rejected_not_patched():
    bad_ir = copy.deepcopy(GOOD_IR)
    bad_ir["instruments"] = ["NQ", "ES"]  # fails pooling invariant

    with pytest.raises(HypothesisInvalidIRError) as exc_info:
        generate_hypothesis(
            _fake_chat("a story", bad_ir), ANOMALY, model_name="gpt-6-astra", temperature=0.7,
        )
    assert any("instruments" in e for e in exc_info.value.errors)


def test_unparsable_response_raises_hypothesis_parse_error():
    def _fn(prompt):
        return ChatResponse(text="not json", model_version="v1")

    with pytest.raises(HypothesisParseError):
        generate_hypothesis(_fn, ANOMALY, model_name="gpt-6-astra", temperature=0.7)


def test_missing_ir_key_raises_hypothesis_parse_error():
    def _fn(prompt):
        return ChatResponse(text=json.dumps({"mechanism": "x"}), model_version="v1")

    with pytest.raises(HypothesisParseError):
        generate_hypothesis(_fn, ANOMALY, model_name="gpt-6-astra", temperature=0.7)


def test_prompt_embeds_the_live_schema_vocabulary_not_a_hardcoded_copy():
    """Guards against the prompt drifting from research.ir.schema's actual
    enforced vocabulary — if schema.py ever adds/removes a recognizable op,
    this fails until the prompt text (via hypothesis.py's render kwargs)
    reflects it, since both are sourced from the same schema module.
    """
    from research.ir import schema as _schema

    captured = {}

    def _fn(prompt: str) -> ChatResponse:
        captured["prompt"] = prompt
        return ChatResponse(text=json.dumps({"mechanism": "x", "ir": GOOD_IR}), model_version="v1")

    generate_hypothesis(_fn, ANOMALY, model_name="gpt-6-astra", temperature=0.7)
    for op in _schema._RECOGNIZABLE_OPS:
        assert op in captured["prompt"]
