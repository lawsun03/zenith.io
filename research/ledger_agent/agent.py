"""The conversational loop: an unprompted health-opening message, a
system prompt carrying the schema and citation rules, and a tool-call
round-trip against research.ledger_agent.tools.run_query.

Nothing here is the read-only enforcement — that's the connection itself
(research.ledger.db.get_readonly_connection). ChatSession refuses to even
start against a connection that isn't in query_only mode, as a fail-loud
guard against this ever accidentally running over a writable connection
(CLAUDE.md rule 12), not as the mechanism itself.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from sqlite3 import Connection
from typing import Any

from research.ledger_agent.health import format_health_summary, loop_health_summary
from research.ledger_agent.kimi_client import KimiToolChatFn, ToolCall
from research.ledger_agent.tools import LEDGER_SCHEMA_REFERENCE, run_query

log = logging.getLogger(__name__)

# A runaway tool-call loop stops here rather than spinning forever against
# a real, billed API.
MAX_TOOL_ROUNDS = 6

SYSTEM_PROMPT = f"""\
You are a research assistant answering questions about a systematic-trading research \
ledger. You have exactly one tool, query_ledger — a read-only SQL SELECT against the \
ledger below. You cannot write to it under any circumstances; if asked to update, delete, \
promote, or otherwise change a row, say plainly that you can't — this connection is \
read-only at the database level, not by your own choice.

{LEDGER_SCHEMA_REFERENCE}

Rules:
- Always query before answering a factual question — never guess at a row id, count, or \
gate outcome from memory or from an earlier answer in this conversation.
- Every claim in your final answer must cite the hypotheses.id (or ensembles.id) values it \
came from, in parentheses.
- A query result is capped at 50 rows. If a result says it was truncated, say so and narrow \
your next query (add a WHERE clause, GROUP BY, or LIMIT) rather than asserting you've seen \
everything.
- If nothing matches, say so plainly — an empty result is an answer, not a reason to guess.
"""


def _is_readonly(conn: Connection) -> bool:
    return conn.execute("PRAGMA query_only").fetchone()[0] == 1


@dataclass
class ChatSession:
    conn: Connection
    chat_fn: KimiToolChatFn
    messages: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not _is_readonly(self.conn):
            raise ValueError(
                "ChatSession requires a read-only connection "
                "(research.ledger.db.get_readonly_connection) — refusing to start "
                "against a connection that could write to the ledger"
            )

    def opening_message(self) -> str:
        """Loop health, surfaced unprompted (PHASE-PROMPTS.md phase 7 item
        3) — computed deterministically before the model says anything,
        then seeded into history as an assistant turn so later questions
        can refer back to it without re-querying."""
        health = loop_health_summary(self.conn)
        text = format_health_summary(health)
        self.messages.append({"role": "system", "content": SYSTEM_PROMPT})
        self.messages.append({"role": "assistant", "content": text})
        return text

    def ask(self, user_message: str) -> str:
        self.messages.append({"role": "user", "content": user_message})
        for _ in range(MAX_TOOL_ROUNDS):
            turn = self.chat_fn(self.messages)
            if turn.is_final:
                self.messages.append({"role": "assistant", "content": turn.content or ""})
                return turn.content or ""

            self.messages.append({
                "role": "assistant",
                "content": turn.content,
                "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": c.arguments}}
                    for c in turn.tool_calls
                ],
            })
            for call in turn.tool_calls:
                self.messages.append({
                    "role": "tool", "tool_call_id": call.id, "content": self._run_tool(call),
                })

        log.warning("tool-call loop hit MAX_TOOL_ROUNDS=%d without a final answer", MAX_TOOL_ROUNDS)
        return "I couldn't settle on an answer within my query budget for this turn — try narrowing the question."

    def _run_tool(self, call: ToolCall) -> str:
        if call.name != "query_ledger":
            return f"ERROR: unknown tool {call.name!r}"
        try:
            args = json.loads(call.arguments)
            sql = args["sql"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            return f"ERROR: unparsable tool arguments: {exc}"
        return run_query(self.conn, sql).to_tool_content()


def start_session(conn: Connection, chat_fn: KimiToolChatFn) -> ChatSession:
    return ChatSession(conn=conn, chat_fn=chat_fn)
