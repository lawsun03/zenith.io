"""Stage b — Astra drafts a falsifiable hypothesis: a stated causal
mechanism plus a strategy IR document (docs/research-loop/README.md's
four-stage cycle).

Every generated IR document is run through research.ir.schema.validate
before this stage returns — CLAUDE.md's "do NOT let generated IR bypass
the phase-2 validator" means an invalid document is a rejected draft here,
never patched into validity or waved through.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

# Reaching into schema's own vocabulary sets rather than duplicating them
# in the prompt template: these are the exact sets validate() enforces, so
# the prompt can never silently drift from what gate 0 actually accepts.
from research.ir import schema as _schema
from research.ir.schema import validate as validate_ir
from research.loop.prompts import PromptTemplate, load_template
from research.loop.providers import ChatFn
from research.loop.triage import TriagedAnomaly

log = logging.getLogger(__name__)

HYPOTHESIS_TEMPLATE = load_template("hypothesis_v1.md")


class HypothesisParseError(RuntimeError):
    """The response wasn't valid JSON, or was missing `mechanism`/`ir`."""


class HypothesisInvalidIRError(RuntimeError):
    """The model's IR document failed research.ir.schema.validate."""

    def __init__(self, errors: list[str]):
        super().__init__(f"generated IR failed validation: {errors}")
        self.errors = errors


@dataclass(frozen=True)
class HypothesisDraft:
    mechanism: str
    ir: dict
    model_name: str
    model_version: str
    prompt_hash: str
    temperature: float


def generate_hypothesis(
    chat_fn: ChatFn,
    anomaly: TriagedAnomaly,
    *,
    model_name: str,
    temperature: float,
    template: PromptTemplate = HYPOTHESIS_TEMPLATE,
) -> HypothesisDraft:
    prompt = template.render(
        instrument=anomaly.instrument,
        session_date=anomaly.session_date,
        regime_label=anomaly.regime_label or "none_identified",
        rationale=anomaly.rationale,
        recognizable_ops=sorted(_schema._RECOGNIZABLE_OPS),
        levels=sorted(_schema._LEVELS),
        stop_types=sorted(_schema._STOP_TYPES),
        target_types=sorted(_schema._TARGET_TYPES),
        sizing_families=sorted(_schema._SIZING_FAMILIES),
    )
    response = chat_fn(prompt)
    try:
        parsed = json.loads(response.text)
        mechanism = str(parsed["mechanism"])
        ir = parsed["ir"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise HypothesisParseError(f"unparsable hypothesis response: {response.text!r}") from exc

    errors = validate_ir(ir)
    if errors:
        raise HypothesisInvalidIRError(errors)

    log.info(
        "hypothesis drafted for %s: %r (model=%s version=%s prompt_hash=%s)",
        anomaly.anomaly_id, ir.get("name"), model_name, response.model_version, template.sha256,
    )
    return HypothesisDraft(
        mechanism=mechanism,
        ir=ir,
        model_name=model_name,
        model_version=response.model_version,
        prompt_hash=template.sha256,
        temperature=temperature,
    )
