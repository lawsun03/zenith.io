"""Stage c — Kimi K3 adversarially reviews a drafted hypothesis
(docs/research-loop/README.md's four-stage cycle).

CRITICAL (CLAUDE.md rule 5 / gates.md): "the reviewer sees the mechanism,
the instrument and the session window — and NOT the backtest. A reviewer
that has seen a good backtest will rationalise it." This is enforced
structurally here, not by convention: `adversarial_review`'s signature has
no parameter through which a trade sequence, Sharpe, gate result, or any
other backtest artifact could reach the prompt — there is no argument to
smuggle one through. tests/test_loop_review.py asserts this by inspecting
the function's signature, not just by checking today's prompt text.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Sequence

from research.loop.prompts import PromptTemplate, load_template
from research.loop.providers import ChatFn

log = logging.getLogger(__name__)

REVIEW_TEMPLATE = load_template("adversarial_review_v1.md")


class ReviewParseError(RuntimeError):
    """The review response wasn't valid JSON or was missing a required field."""


@dataclass(frozen=True)
class ReviewVerdict:
    survives: bool
    falsifier: str
    critique: str
    model_version: str
    prompt_hash: str


def adversarial_review(
    chat_fn: ChatFn,
    *,
    mechanism: str,
    instruments: Sequence[str],
    session_start: str,
    session_end: str,
    session_tz: str,
    template: PromptTemplate = REVIEW_TEMPLATE,
) -> ReviewVerdict:
    prompt = template.render(
        mechanism=mechanism,
        instruments=", ".join(instruments),
        session_start=session_start,
        session_end=session_end,
        session_tz=session_tz,
    )
    response = chat_fn(prompt)
    try:
        parsed = json.loads(response.text)
        survives = bool(parsed["survives"])
        falsifier = str(parsed["falsifier"])
        critique = str(parsed.get("critique", ""))
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ReviewParseError(f"unparsable review response: {response.text!r}") from exc

    log.info(
        "adversarial review: survives=%s falsifier=%r (model_version=%s prompt_hash=%s)",
        survives, falsifier, response.model_version, template.sha256,
    )
    return ReviewVerdict(
        survives=survives,
        falsifier=falsifier,
        critique=critique,
        model_version=response.model_version,
        prompt_hash=template.sha256,
    )
