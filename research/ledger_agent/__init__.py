"""Conversational layer over the research ledger, backed by Kimi K3
(docs/research-loop/PHASE-PROMPTS.md phase 7).

Read-only end to end: research.ledger.db.get_readonly_connection enforces
that at the SQLite connection level (PRAGMA query_only = ON), not by
inspecting what SQL the model asks for. The agent queries and narrates —
it is never handed bulk rows to summarize itself; every query result
returned to the model is already row-capped (research.ledger_agent.tools).
"""
