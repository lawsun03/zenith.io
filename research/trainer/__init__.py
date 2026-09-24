"""Replay trainer — phase 6 of the research loop.

A validated ensemble becomes a bar-by-bar drill: the future is hidden,
the trainee answers three questions per decision point (is this a setup;
if so which direction; where does the stop go), and is scored against the
IR's own answer, never against what the market did. See
docs/research-loop/PHASE-PROMPTS.md "Phase 6 — Trainer" and CLAUDE.md's
scoring rule.
"""
