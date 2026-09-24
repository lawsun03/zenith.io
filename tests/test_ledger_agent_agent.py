"""End-to-end conversational loop (research.ledger_agent.agent), driven by
a scripted fake KimiToolChatFn — no real Moonshot API call, matching how
every research.loop stage test injects a fake ChatFn. What's under test is
the WIRING (tool call -> real SQL against a real seeded ledger -> result
fed back -> final answer), not whether a live Kimi asks a clever query.

Phase 7's own acceptance tests:
- "What has been tested on GC?" returns an accurate answer citing row ids.
- Health numbers appear without being asked for.
"""
from __future__ import annotations

import json

import pytest

from research.ledger.api import append_hypothesis, record_loop_pbo
from research.ledger.db import get_connection, get_readonly_connection
from research.ledger_agent.agent import ChatSession, start_session
from research.ledger_agent.kimi_client import AgentTurn, ToolCall

SAMPLE_IR = {"ir_version": "1.0", "name": "agent-fixture", "instruments": ["NQ", "ES", "GC"]}


@pytest.fixture
def ro_conn(tmp_path):
    path = tmp_path / "ledger.db"
    conn = get_connection(path)
    ids = [
        append_hypothesis(
            conn, ir={**SAMPLE_IR, "name": f"agent-fixture-{i}"}, mechanism=f"mechanism {i}",
            falsifier="f", model_name="astra", model_version="v1", prompt_hash="h",
            temperature=0.7, data_range="x", param_grid={}, n_variants_swept=1,
            trial_count_at_test=i,
        )
        for i in range(3)
    ]
    record_loop_pbo(conn, 0.42)
    conn.close()
    ro = get_readonly_connection(path)
    yield ro, ids, path
    ro.close()


class _ScriptedChatFn:
    """Replays a fixed sequence of AgentTurns, one per call — a fake
    KimiToolChatFn a test can hand-script exactly like
    research.loop.providers' fakes hand ChatResponse sequences."""

    def __init__(self, turns: list[AgentTurn]):
        self._turns = list(turns)
        self.calls: list[list[dict]] = []

    def __call__(self, messages: list[dict]) -> AgentTurn:
        self.calls.append([dict(m) for m in messages])
        return self._turns.pop(0)


def test_what_has_been_tested_on_gc_cites_row_ids(ro_conn):
    conn, ids, path = ro_conn
    call_sql = "SELECT id, mechanism, outcome FROM hypotheses WHERE ir_json LIKE '%\"GC\"%'"
    fake = _ScriptedChatFn([
        AgentTurn(
            content=None,
            tool_calls=[ToolCall(id="call_1", name="query_ledger", arguments=json.dumps({"sql": call_sql}))],
        ),
        AgentTurn(content=(
            f"Three candidates have been tested on GC (all rows pool NQ+ES+GC — "
            f"CLAUDE.md domain invariant 2, no strategy trades GC alone): "
            f"{ids[0]}, {ids[1]}, {ids[2]}, all currently outcome=rejected."
        )),
    ])
    session = ChatSession(conn=conn, chat_fn=fake)
    session.opening_message()
    answer = session.ask("What has been tested on GC?")

    assert all(i in answer for i in ids)
    assert "rejected" in answer
    # the tool actually ran against the real ledger, not a canned string
    tool_message = session.messages[-2]
    assert tool_message["role"] == "tool"
    for i in ids:
        assert i in tool_message["content"]


def test_opening_message_surfaces_health_without_being_asked(ro_conn):
    conn, ids, path = ro_conn
    session = start_session(conn, chat_fn=_ScriptedChatFn([]))
    opening = session.opening_message()

    assert "LOOP HEALTH" in opening
    assert "0.420" in opening  # loop_pbo seeded in the fixture
    assert "hypotheses logged this year" in opening
    # seeded entirely by opening_message() — no model call happened
    assert session.messages[0]["role"] == "system"
    assert session.messages[1] == {"role": "assistant", "content": opening}


def test_an_attempt_to_get_the_agent_to_write_fails_at_the_connection(ro_conn):
    """The model can ASK for a write via the tool; the tool executes
    against a read-only connection, so it fails there — the agent then has
    to tell the user it can't, it can't silently succeed."""
    conn, ids, path = ro_conn
    write_sql = f"UPDATE hypotheses SET outcome = 'promoted' WHERE id = '{ids[0]}'"
    fake = _ScriptedChatFn([
        AgentTurn(
            content=None,
            tool_calls=[ToolCall(id="call_1", name="query_ledger", arguments=json.dumps({"sql": write_sql}))],
        ),
        AgentTurn(content="I can't do that — this connection is read-only."),
    ])
    session = ChatSession(conn=conn, chat_fn=fake)
    answer = session.ask("Mark the first GC candidate as promoted.")

    tool_message = session.messages[-2]
    assert tool_message["role"] == "tool"
    assert "ERROR" in tool_message["content"]
    assert "read-only" in answer.lower() or "can't" in answer.lower()

    # and the row is genuinely unchanged
    check = get_connection(path)
    row = check.execute("SELECT outcome FROM hypotheses WHERE id = ?", (ids[0],)).fetchone()
    assert row["outcome"] == "rejected"


def test_tool_loop_gives_up_after_max_rounds_instead_of_spinning_forever(ro_conn):
    from research.ledger_agent.agent import MAX_TOOL_ROUNDS
    conn, ids, path = ro_conn
    endless_call = AgentTurn(
        content=None,
        tool_calls=[ToolCall(id="x", name="query_ledger", arguments=json.dumps({"sql": "SELECT 1"}))],
    )
    fake = _ScriptedChatFn([endless_call] * MAX_TOOL_ROUNDS)
    session = ChatSession(conn=conn, chat_fn=fake)
    answer = session.ask("loop forever")
    assert "query budget" in answer.lower()
