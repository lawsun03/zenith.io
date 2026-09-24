"""research.anomaly.grok_client's pure parsing helpers — the response-shape
migration to xAI's Responses API (docs.x.ai/developers/tools/web-search,
docs.x.ai/developers/cost-tracking). No real HTTP call in any of these;
make_xai_label_fn itself stays untested here for the same reason
research.data.ingest.make_databento_fetch_fn is — a real call costs money.
"""
from __future__ import annotations

import pytest

from research.anomaly.grok_client import (
    FATAL_STATUS_CODES,
    _cost_from_usage,
    _extract_json_object,
    _extract_output_text_and_citations,
)


def _response(text: str, annotations: list[dict] | None = None) -> dict:
    return {
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": text, "annotations": annotations or []},
                ],
            }
        ],
    }


def test_extract_output_text_and_citations_happy_path():
    body = _response(
        '{"category": "scheduled_macro"}',
        annotations=[
            {"type": "url_citation", "url": "https://example.com/a", "title": "1"},
            {"type": "url_citation", "url": "https://example.com/b", "title": "2"},
        ],
    )
    text, citations = _extract_output_text_and_citations(body)
    assert text == '{"category": "scheduled_macro"}'
    assert citations == ["https://example.com/a", "https://example.com/b"]


def test_extract_output_text_and_citations_no_annotations():
    body = _response('{"category": "none_identified"}')
    text, citations = _extract_output_text_and_citations(body)
    assert text == '{"category": "none_identified"}'
    assert citations == []


def test_extract_output_text_and_citations_skips_non_text_blocks():
    body = {
        "output": [
            {"type": "message", "content": [{"type": "reasoning", "text": "thinking..."}]},
            {"type": "message", "content": [{"type": "output_text", "text": "the answer", "annotations": []}]},
        ],
    }
    text, _ = _extract_output_text_and_citations(body)
    assert text == "the answer"


def test_extract_output_text_and_citations_uses_the_last_block_not_the_first():
    """Verified against a real grok-4.7 response (2026-09): a completed
    tool-using response's `output` array includes an early narration
    message ("I'll look up...") as its own output_text block before the
    actual final answer — taking the first block silently returns the
    narration instead of the answer."""
    body = {
        "output": [
            {"type": "message", "content": [
                {"type": "output_text", "text": "I'll look up what happened that day.", "annotations": []},
            ]},
            {"type": "reasoning", "summary": [{"text": "thinking...", "type": "summary_text"}]},
            {"type": "message", "content": [
                {"type": "output_text", "text": '{"category": "scheduled_macro", "confidence": 0.8}',
                 "annotations": [{"type": "url_citation", "url": "https://example.com/cpi", "title": "1"}]},
            ]},
        ],
    }
    text, citations = _extract_output_text_and_citations(body)
    assert text == '{"category": "scheduled_macro", "confidence": 0.8}'
    assert citations == ["https://example.com/cpi"]


def test_extract_output_text_and_citations_raises_when_no_output_text_block():
    body = {"output": [{"type": "message", "content": [{"type": "reasoning", "text": "x"}]}]}
    with pytest.raises(KeyError):
        _extract_output_text_and_citations(body)


def test_extract_json_object_parses_clean_json():
    parsed = _extract_json_object('{"category": "scheduled_macro", "confidence": 0.9}')
    assert parsed == {"category": "scheduled_macro", "confidence": 0.9}


def test_extract_json_object_tolerates_surrounding_prose():
    text = 'Sure, here is my answer:\n{"category": "quarterly_event", "confidence": 0.5}\nHope that helps.'
    parsed = _extract_json_object(text)
    assert parsed["category"] == "quarterly_event"


def test_extract_json_object_tolerates_markdown_fence():
    text = '```json\n{"category": "idiosyncratic_shock", "confidence": 0.7}\n```'
    parsed = _extract_json_object(text)
    assert parsed["category"] == "idiosyncratic_shock"


def test_extract_json_object_raises_when_no_json_present():
    with pytest.raises(ValueError):
        _extract_json_object("I couldn't find anything about that day.")


def test_cost_from_usage_prefers_real_cost_in_usd_ticks():
    usage = {"input_tokens": 199, "output_tokens": 1, "cost_in_usd_ticks": 158500}
    cost, was_estimated = _cost_from_usage(usage)
    assert cost == pytest.approx(0.0000158500)
    assert was_estimated is False


def test_cost_from_usage_falls_back_to_token_and_tool_estimate():
    usage = {
        "input_tokens": 1000, "output_tokens": 500,
        "server_side_tool_usage": {"web_search_calls": 2, "x_search_calls": 1},
    }
    cost, was_estimated = _cost_from_usage(usage)
    assert was_estimated is True
    # 1000 * 2e-6 + 500 * 6e-6 + 3 * 0.005
    assert cost == pytest.approx(1000 * (2.00 / 1_000_000) + 500 * (6.00 / 1_000_000) + 3 * (5.00 / 1_000))


def test_cost_from_usage_floors_out_when_genuinely_no_usage_data():
    cost, was_estimated = _cost_from_usage({})
    assert was_estimated is True
    assert cost > 0


def test_fatal_status_codes_are_exactly_the_ones_that_never_succeed_on_retry():
    assert FATAL_STATUS_CODES == {401, 403, 404, 410}
