"""The ensemble builder (docs/research-loop/gates.md, "After the gates:
blend, do not rank"; CLAUDE.md domain invariant 3).

`build_ensemble` takes EVERY survivor it is given and assigns each one an
equal weight. It has no parameter that could mean "how many to keep," "sort
by," or "top N" — there is deliberately nothing here to misuse into
selection. Carver's gold-futures result is the reason: annual-best selection
scored 0.07 Sharpe, random selection 0.20, blending everything 0.33.
Selection is not a milder version of blending; it is worse than doing
nothing.

tests/test_gates_no_selection.py statically scans this file (and pipeline.py)
for `sorted(`/`.sort(`/`heapq.nlargest`/`heapq.nsmallest` and fails the suite
if any appear — so don't add one, even for something that looks unrelated
to promotion (e.g. "sort for a stable display order"). Do that at the
call site, on a copy, outside this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence


@dataclass(frozen=True)
class EnsembleMember:
    hypothesis_id: str
    ir_doc: dict
    weight: Decimal


@dataclass(frozen=True)
class Ensemble:
    family: str
    members: tuple[EnsembleMember, ...]

    @property
    def member_count(self) -> int:
        return len(self.members)


def build_ensemble(family: str, survivors: Sequence[tuple[str, dict]]) -> Ensemble:
    """`survivors` is (hypothesis_id, ir_doc) for every candidate that
    cleared gates 0-9 within `family` — membership and order come entirely
    from the caller. Every member gets weight 1/N; N is len(survivors),
    nothing more."""
    if not survivors:
        raise ValueError("cannot build an ensemble from zero survivors")
    n = len(survivors)
    weight = Decimal(1) / Decimal(n)
    members = tuple(EnsembleMember(hypothesis_id=hid, ir_doc=doc, weight=weight)
                     for hid, doc in survivors)
    return Ensemble(family=family, members=members)
