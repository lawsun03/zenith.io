"""Stage d — Kimi K3 enumerates IR variants around a surviving hypothesis
(docs/research-loop/README.md's four-stage cycle).

Produces the param_grid / n_variants_swept research/ledger/api.py's
append_hypothesis records — CLAUDE.md rule 7 / docs/research-loop/gates.md:
"A sweep of 90 variants counts as 90 trials, not 1." Every variant is
independently re-validated through research.ir.schema — a variant that
fails validation is dropped and logged, never silently coerced into
validity (the same "do NOT let generated IR bypass the phase-2 validator"
constraint as stage b).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from research.ir.schema import validate as validate_ir
from research.loop.prompts import PromptTemplate, load_template
from research.loop.providers import ChatFn

log = logging.getLogger(__name__)

VARIANTS_TEMPLATE = load_template("variants_v1.md")

MAX_VARIANTS = 12


class VariantParseError(RuntimeError):
    """The variants response wasn't valid JSON or was missing a required field."""


@dataclass(frozen=True)
class VariantEnumeration:
    param_grid: dict[str, Any]
    variant_irs: tuple[dict, ...]
    n_variants_swept: int
    model_version: str
    prompt_hash: str


def enumerate_variants(
    chat_fn: ChatFn,
    *,
    mechanism: str,
    base_ir: dict,
    max_variants: int = MAX_VARIANTS,
    template: PromptTemplate = VARIANTS_TEMPLATE,
) -> VariantEnumeration:
    prompt = template.render(
        mechanism=mechanism,
        base_ir_json=json.dumps(base_ir, sort_keys=True),
        max_variants=max_variants,
    )
    response = chat_fn(prompt)
    try:
        parsed = json.loads(response.text)
        param_grid = parsed["param_grid"]
        raw_variants = parsed["variants"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise VariantParseError(f"unparsable variants response: {response.text!r}") from exc

    valid_variants = []
    for i, variant in enumerate(raw_variants[:max_variants]):
        errors = validate_ir(variant)
        if errors:
            log.warning("variant %d failed IR validation, dropped: %s", i, errors)
            continue
        valid_variants.append(variant)

    if not valid_variants:
        # The base hypothesis itself is always at least one trial — a
        # variant stage that produced nothing valid must not zero out the
        # trial count for a hypothesis that did pass validation and review.
        log.warning("no valid variants produced — falling back to the base IR alone")
        valid_variants = [base_ir]

    log.info(
        "variant enumeration: %d/%d variants valid (model_version=%s prompt_hash=%s)",
        len(valid_variants), len(raw_variants), response.model_version, template.sha256,
    )
    return VariantEnumeration(
        param_grid=param_grid,
        variant_irs=tuple(valid_variants),
        n_variants_swept=len(valid_variants),
        model_version=response.model_version,
        prompt_hash=template.sha256,
    )
