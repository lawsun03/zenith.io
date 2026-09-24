"""Stage -> model -> provider wiring (research/loop/config.py).

Only tests the wiring logic itself — `make_stage_chat_fn` returns a
closure without making any network call (research.loop.providers'
make_*_chat_fn factories don't import `requests` until the closure is
actually invoked), so this is safe to exercise directly with fake keys.
"""
from __future__ import annotations

import pytest

from research.loop.config import (
    ASTRA_MODEL,
    KIMI_MODEL,
    MODEL_PROVIDER,
    STAGE_ADVERSARIAL_REVIEW,
    STAGE_HYPOTHESIS,
    STAGE_MODELS,
    STAGE_TRIAGE,
    STAGE_VARIANT_ENUMERATION,
    make_stage_chat_fn,
)


def test_every_stage_has_a_model_and_every_model_has_a_provider():
    for stage, model in STAGE_MODELS.items():
        assert model in MODEL_PROVIDER, f"stage {stage!r}'s model {model!r} has no provider mapping"


def test_hypothesis_stage_uses_astra_and_others_use_kimi():
    assert STAGE_MODELS[STAGE_HYPOTHESIS] == ASTRA_MODEL
    assert STAGE_MODELS[STAGE_TRIAGE] == KIMI_MODEL
    assert STAGE_MODELS[STAGE_ADVERSARIAL_REVIEW] == KIMI_MODEL
    assert STAGE_MODELS[STAGE_VARIANT_ENUMERATION] == KIMI_MODEL


def test_make_stage_chat_fn_builds_a_callable_given_the_right_key():
    chat_fn = make_stage_chat_fn(STAGE_TRIAGE, {"moonshot": "fake-key"})
    assert callable(chat_fn)


def test_make_stage_chat_fn_raises_on_missing_key():
    with pytest.raises(KeyError):
        make_stage_chat_fn(STAGE_HYPOTHESIS, {"moonshot": "fake-key"})  # hypothesis needs openai, not moonshot
