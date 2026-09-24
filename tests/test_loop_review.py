"""Stage c — adversarial review (research/loop/review.py).

CLAUDE.md rule 5 / docs/research-loop/README.md: the reviewer must never
see a backtest result. Enforced by review.adversarial_review's own
signature, not by scanning prompt text after the fact — so the guarantee
holds even if the prompt template is edited later.
"""
from __future__ import annotations

import inspect
import json

import pytest

from research.loop.providers import ChatResponse
from research.loop.review import ReviewParseError, adversarial_review

_BACKTEST_PARAM_NAMES = {
    "trades", "trade_sequence", "sharpe", "sharpe_oos", "sharpe_is", "backtest",
    "backtest_result", "gate_results", "pnl", "performance", "combine_payout_prob",
    "candidate_inputs", "candidate_outcome",
}


def test_signature_has_no_way_to_pass_backtest_data():
    params = set(inspect.signature(adversarial_review).parameters)
    leaked = params & _BACKTEST_PARAM_NAMES
    assert not leaked, f"adversarial_review accepts backtest-shaped params: {leaked}"

    # and the full parameter list is exactly the mechanism/instrument/session
    # context README promises the reviewer — nothing else, ever.
    assert params == {"chat_fn", "mechanism", "instruments", "session_start", "session_end", "session_tz", "template"}


def _fake_chat(survives: bool, falsifier: str = "a stated falsifier", model_version="kimi-k3-2026-08-01"):
    def _fn(prompt: str) -> ChatResponse:
        return ChatResponse(
            text=json.dumps({"survives": survives, "falsifier": falsifier, "critique": "c"}),
            model_version=model_version,
        )
    return _fn


def test_survives_true_is_parsed():
    verdict = adversarial_review(
        _fake_chat(True), mechanism="m", instruments=["NQ", "ES", "GC"],
        session_start="08:30", session_end="11:00", session_tz="America/New_York",
    )
    assert verdict.survives is True
    assert verdict.falsifier == "a stated falsifier"
    assert verdict.model_version == "kimi-k3-2026-08-01"


def test_survives_false_still_returns_a_falsifier():
    verdict = adversarial_review(
        _fake_chat(False, falsifier="if X happens the mechanism is wrong"),
        mechanism="m", instruments=["NQ", "ES", "GC"],
        session_start="08:30", session_end="11:00", session_tz="America/New_York",
    )
    assert verdict.survives is False
    assert verdict.falsifier == "if X happens the mechanism is wrong"


def test_unparsable_response_raises_review_parse_error():
    def _fn(prompt):
        return ChatResponse(text="not json", model_version="v1")

    with pytest.raises(ReviewParseError):
        adversarial_review(
            _fn, mechanism="m", instruments=["NQ", "ES", "GC"],
            session_start="08:30", session_end="11:00", session_tz="America/New_York",
        )


def test_missing_falsifier_raises_review_parse_error():
    def _fn(prompt):
        return ChatResponse(text=json.dumps({"survives": True}), model_version="v1")

    with pytest.raises(ReviewParseError):
        adversarial_review(
            _fn, mechanism="m", instruments=["NQ", "ES", "GC"],
            session_start="08:30", session_end="11:00", session_tz="America/New_York",
        )


def test_prompt_never_mentions_backtest_vocabulary():
    """Defense in depth on top of the signature check: even the rendered
    prompt text carries none of the words a backtest-influenced review
    would need."""
    captured = {}

    def _fn(prompt: str) -> ChatResponse:
        captured["prompt"] = prompt
        return ChatResponse(text=json.dumps({"survives": True, "falsifier": "f", "critique": "c"}), model_version="v1")

    adversarial_review(
        _fn, mechanism="m", instruments=["NQ", "ES", "GC"],
        session_start="08:30", session_end="11:00", session_tz="America/New_York",
    )
    # Not "backtest" itself — the prompt legitimately tells the reviewer
    # that no backtest exists in its context — but no actual performance
    # figure or metric name a backtest would produce.
    lowered = captured["prompt"].lower()
    for banned in ("sharpe", "win rate", "profit factor", "pnl", "gate_result"):
        assert banned not in lowered
