"""Provider clients for the hypothesis loop's three models
(docs/research-loop/PHASE-PROMPTS.md phase 5): OpenAI (gpt-6-astra),
Moonshot (kimi-k3) and xAI (grok-4.7) — each hit directly, no gateway.

Same dependency-injection seam as research.data.ingest's FetchFn and
research.anomaly.grok_client's LabelFn: `ChatFn` is what every stage
module in research.loop takes and what tests inject a fake across;
make_*_chat_fn are the three real adapters. None are exercised by tests —
same reasoning as make_xai_label_fn: a real call costs money and needs a
funded API key, and `requests` is imported lazily inside
`_post_chat_completion` so importing this module never requires it.

Keys are read from the environment only, never hardcoded — callers
(scripts/run_hypothesis_loop.py) load a .env file into the environment
before building a ChatFn, the same convention scripts/run_anomaly_pass.py
already uses for XAI_API_KEY.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger(__name__)

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
MOONSHOT_CHAT_COMPLETIONS_URL = "https://api.moonshot.ai/v1/chat/completions"
XAI_CHAT_COMPLETIONS_URL = "https://api.x.ai/v1/chat/completions"

ASTRA_MODEL = "gpt-6-astra"
KIMI_MODEL = "kimi-k3"
GROK_MODEL = "grok-4.7"


@dataclass(frozen=True)
class ChatResponse:
    text: str
    model_version: str  # the provider's own exact-version string for this call


# prompt -> response. Model and temperature are baked in by the factory
# that built the ChatFn (exactly like grok_client.make_xai_label_fn binds
# XAI_MODEL) — a stage module never chooses a model itself, config.py does.
ChatFn = Callable[[str], ChatResponse]


class ChatCallError(RuntimeError):
    """A single chat completion call failed or returned an unparsable
    envelope. Distinct from a stage's own *ParseError, which is about the
    JSON *content* of a successful response not matching what the stage
    expected — this is about the HTTP/transport layer, recoverable the
    same way LabelFetchError is: the caller logs it and skips this item,
    it does not crash the cycle.
    """


def _post_chat_completion(
    url: str, api_key: str, model: str, temperature: float | None, prompt: str, *, timeout: int = 120,
) -> ChatResponse:
    import requests

    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }
    # gpt-6-astra and kimi-k3 both reject any temperature but the default
    # (1) with a 400 (verified live 2026-09-24), so OpenAI/Moonshot factories
    # pass None and the field is omitted rather than sent.
    if temperature is not None:
        payload["temperature"] = temperature
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        text = body["choices"][0]["message"]["content"]
        model_version = body.get("model", model)
    except Exception as exc:  # noqa: BLE001 — any failure here is a single-call failure, not a crash
        raise ChatCallError(f"{url} ({model}): {exc}") from exc
    return ChatResponse(text=text, model_version=model_version)


def make_openai_chat_fn(api_key: str, *, temperature: float, model: str = ASTRA_MODEL) -> ChatFn:
    def _chat(prompt: str) -> ChatResponse:
        return _post_chat_completion(OPENAI_CHAT_COMPLETIONS_URL, api_key, model, None, prompt)

    return _chat


def make_moonshot_chat_fn(api_key: str, *, temperature: float, model: str = KIMI_MODEL) -> ChatFn:
    def _chat(prompt: str) -> ChatResponse:
        return _post_chat_completion(MOONSHOT_CHAT_COMPLETIONS_URL, api_key, model, None, prompt)

    return _chat


def make_xai_chat_fn(api_key: str, *, temperature: float, model: str = GROK_MODEL) -> ChatFn:
    """Plain chat completion, no Live Search — distinct from
    research.anomaly.grok_client.make_xai_label_fn, which enables search
    for regime labelling. Not currently wired into any research.loop stage
    (docs/research-loop/README.md's four-stage cycle uses only Kimi K3 and
    Astra); built anyway per phase-5 scope item 1, so routing a stage
    through Grok later is the config change research/loop/config.py
    promises, not a new provider client.
    """

    def _chat(prompt: str) -> ChatResponse:
        return _post_chat_completion(XAI_CHAT_COMPLETIONS_URL, api_key, model, temperature, prompt)

    return _chat
