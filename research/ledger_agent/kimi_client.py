"""Tool-calling Moonshot (Kimi K3) client for the ledger chat agent.

Separate from research.loop.providers.ChatFn on purpose: that type is a
plain prompt-in/text-out contract shared by four single-shot JSON-drafting
stages (research/loop/hypothesis.py etc.), and changing its shape to carry
tool calls and multi-turn message history would ripple into all of them
for a capability only this module needs. Reuses providers.py's model name
and URL constants rather than redefining them, so there is exactly one
place either changes.

Moonshot's Chat Completions API is OpenAI-tool-schema-compatible (the same
{"type": "function", "function": {...}} shape research.ledger_agent.tools.
QUERY_TOOL_SCHEMA is already written in) — this module is the one place
that assumption lives.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from research.loop.providers import KIMI_MODEL, MOONSHOT_CHAT_COMPLETIONS_URL, ChatCallError


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text, as the API sends it — the caller parses it


@dataclass(frozen=True)
class AgentTurn:
    """One model response: either a final message to show the user, or one
    or more tool calls to execute and feed back."""
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model_version: str = ""

    @property
    def is_final(self) -> bool:
        return not self.tool_calls


# messages (OpenAI-style role/content/tool_calls/tool_call_id dicts), tools
# schema -> AgentTurn. The seam tests inject a fake across, exactly like
# research.loop.providers.ChatFn.
KimiToolChatFn = Callable[[list[dict[str, Any]]], AgentTurn]


def make_kimi_tool_chat_fn(
    api_key: str, *, tools: list[dict[str, Any]], temperature: float = 0.2, model: str = KIMI_MODEL,
) -> KimiToolChatFn:
    def _chat(messages: list[dict[str, Any]]) -> AgentTurn:
        import requests

        payload: dict[str, Any] = {
            "model": model, "messages": messages, "tools": tools,  # kimi-k3 only accepts the default temperature
        }
        try:
            resp = requests.post(
                MOONSHOT_CHAT_COMPLETIONS_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload, timeout=120,
            )
            resp.raise_for_status()
            body = resp.json()
            message = body["choices"][0]["message"]
            model_version = body.get("model", model)
        except Exception as exc:  # noqa: BLE001 — a single-call failure, not a crash
            raise ChatCallError(f"{MOONSHOT_CHAT_COMPLETIONS_URL} ({model}): {exc}") from exc

        raw_calls = message.get("tool_calls") or []
        tool_calls = [
            ToolCall(id=c["id"], name=c["function"]["name"], arguments=c["function"]["arguments"])
            for c in raw_calls
        ]
        return AgentTurn(content=message.get("content"), tool_calls=tool_calls, model_version=model_version)

    return _chat
