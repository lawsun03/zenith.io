"""Stage -> model -> provider wiring for the hypothesis loop.

The whole point of keeping research.loop's orchestration thin
(docs/research-loop/PHASE-PROMPTS.md phase 5: "keep the orchestration
layer thin enough that swapping a model is a config change, not a
rewrite") lives in this one file: change a value here, not a line of
research/loop/cycle.py or any stage module.
"""
from __future__ import annotations

from research.loop.providers import (
    ASTRA_MODEL,
    GROK_MODEL,
    KIMI_MODEL,
    ChatFn,
    make_moonshot_chat_fn,
    make_openai_chat_fn,
    make_xai_chat_fn,
)

STAGE_TRIAGE = "triage"
STAGE_HYPOTHESIS = "hypothesis"
STAGE_ADVERSARIAL_REVIEW = "adversarial_review"
STAGE_VARIANT_ENUMERATION = "variant_enumeration"

# docs/research-loop/README.md's pipeline: Kimi triages, reviews and
# enumerates variants; Astra is the one stage that has to originate a
# genuinely novel, falsifiable idea rather than judge or vary one.
STAGE_MODELS: dict[str, str] = {
    STAGE_TRIAGE: KIMI_MODEL,
    STAGE_HYPOTHESIS: ASTRA_MODEL,
    STAGE_ADVERSARIAL_REVIEW: KIMI_MODEL,
    STAGE_VARIANT_ENUMERATION: KIMI_MODEL,
}

# Lower temperature where the job is judgment/consistency (triage,
# adversarial review), higher where it's meant to explore (drafting the
# hypothesis itself, enumerating variants around it).
STAGE_TEMPERATURE: dict[str, float] = {
    STAGE_TRIAGE: 0.2,
    STAGE_HYPOTHESIS: 0.7,
    STAGE_ADVERSARIAL_REVIEW: 0.2,
    STAGE_VARIANT_ENUMERATION: 0.4,
}

MODEL_PROVIDER: dict[str, str] = {
    ASTRA_MODEL: "openai",
    KIMI_MODEL: "moonshot",
    GROK_MODEL: "xai",
}

PROVIDER_API_KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "xai": "XAI_API_KEY",
}

PROVIDER_CHAT_FACTORIES = {
    "openai": make_openai_chat_fn,
    "moonshot": make_moonshot_chat_fn,
    "xai": make_xai_chat_fn,
}


def make_stage_chat_fn(stage: str, api_keys: dict[str, str]) -> ChatFn:
    """Build the ChatFn for `stage` per STAGE_MODELS/MODEL_PROVIDER.

    `api_keys` maps provider name -> key (e.g. {"openai": "sk-...",
    "moonshot": "..."}) — callers assemble this from the environment
    (scripts/run_hypothesis_loop.py); this function never reads os.environ
    itself, so it stays a pure function of its inputs and is trivially
    testable with fake keys.
    """
    model = STAGE_MODELS[stage]
    provider = MODEL_PROVIDER[model]
    factory = PROVIDER_CHAT_FACTORIES[provider]
    api_key = api_keys.get(provider)
    if not api_key:
        raise KeyError(
            f"stage {stage!r} needs model {model!r} via provider {provider!r}, "
            f"but no key was provided (expected env var {PROVIDER_API_KEY_ENV[provider]})"
        )
    return factory(api_key, temperature=STAGE_TEMPERATURE[stage], model=model)
