"""Regime labels: classification metadata only.

CLAUDE.md domain invariant #5 / docs/research-loop/gates.md: `regime_label`
may be used to include/exclude sessions from a validation sample and to
group sessions for validation, but must NEVER be readable from the IR
predicate vocabulary — a label derived from knowing how a day turned out
is lookahead bias.

REGIME_LEDGER_FIELD is the literal string "regime_label", the same name
docs/research-loop/ledger.sql gives `hypotheses.regime_label` and the same
name research.ir.schema._BANNED_TERMS already rejects anywhere in an IR
document (case-insensitively, key or value). It's exported here — rather
than each module hardcoding the string — so tests/test_anomaly_labels.py
can assert the validator rejects *this exact* field name and the two
modules can't silently drift apart if either one is renamed later.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

REGIME_LEDGER_FIELD = "regime_label"

Category = Literal["scheduled_macro", "quarterly_event", "idiosyncratic_shock", "none_identified"]

CATEGORIES: tuple[Category, ...] = (
    "scheduled_macro",
    "quarterly_event",
    "idiosyncratic_shock",
    "none_identified",
)


@dataclass(frozen=True)
class RegimeLabel:
    """One session's regime label. `category` is what's written to
    `hypotheses.regime_label` (phase 5); `description`, `confidence` and
    `sources` are the supporting detail a human reviewer can check.
    """

    category: Category
    description: str
    confidence: float
    sources: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown regime category {self.category!r}, must be one of {CATEGORIES}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")


def parse_label_response(raw: dict) -> RegimeLabel:
    """Parse and validate a structured {category, description, confidence,
    sources} response.

    Raises ValueError on anything malformed. A label that can't be parsed
    is a failed label, not a silent "none_identified" — CLAUDE.md rule 12:
    a gate (or label) that cannot be evaluated is a failure, not a default
    pass. Callers (research.anomaly.grok_client) turn this into a
    LabelFetchError that the pipeline logs and skips, rather than
    defaulting the session to some category it was never actually told.
    """
    try:
        category = raw["category"]
        description = str(raw["description"])
        confidence = float(raw["confidence"])
        sources = tuple(str(s) for s in raw.get("sources", []))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"malformed label response: {raw!r}") from exc
    return RegimeLabel(category=category, description=description, confidence=confidence, sources=sources)
