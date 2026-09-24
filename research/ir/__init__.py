"""Strategy IR interpreter — phase 2 of the research loop.

A strategy is a JSON document conforming to
docs/research-loop/strategy-ir.schema.json. This package validates it,
evaluates its predicates as pure functions of a bar window, walks bars to
produce a trade sequence, and (separately) sizes that sequence against an
account config. See CLAUDE.md invariant #1: strategies are data, never code.
"""
