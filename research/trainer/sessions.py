"""Drill session orchestration and in-process state.

Builds a session's decision-point queue (data loading + extraction +
sampling), holds it and the running answers in memory while the trainee
works through it, and produces the final tally to persist. Nothing here
is written to the ledger incrementally — only complete_session's callers
write drill_sessions/drill_decisions, once, so an abandoned session leaves
no partial row behind to confuse the fidelity trend.
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.sim.events import Bar
from app.sim.paper import TICK_SIZE
from research.data.loader import load_bars
from research.gates.ensemble import Ensemble
from research.trainer.decision_points import bars_from_frame, extract_decision_points
from research.trainer.regime import session_dates_for_label
from research.trainer.sampling import (
    Candidate, DecisionKey, build_candidate_pool, sample_queue,
)
from research.trainer.scoring import Answer, GroundTruth, Score, score_decision, tally_session

ET_TZ = "America/New_York"

# Research strategies are always exactly NQ/ES/GC (CLAUDE.md invariant 2 —
# pooled fitting, no per-instrument parameters), whose tick SIZE (not tick
# VALUE — those differ between full and micro contracts, size doesn't)
# matches the micro variants already calibrated in app/sim/paper.py.
# Reusing that dict rather than a second, independent set of constants is
# exactly what CLAUDE.md's "one 10x tick-value bug" note is warning about.
RESEARCH_TICK_SIZE: dict[str, Decimal] = {
    "NQ": TICK_SIZE["MNQ"],
    "ES": TICK_SIZE["MES"],
    "GC": TICK_SIZE["GC"],
}


@dataclass(frozen=True)
class DecisionRecord:
    candidate: Candidate
    answer: Answer
    score: Score


@dataclass
class TrainerSession:
    session_id: str
    ensemble_id: str
    queue: list[Candidate]
    cursor: int = 0
    records: list[DecisionRecord] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return self.cursor >= len(self.queue)

    def current(self) -> Candidate | None:
        return None if self.done else self.queue[self.cursor]

    def answer_current(self, answer: Answer) -> Score:
        candidate = self.current()
        if candidate is None:
            raise ValueError("no current decision point — session already complete")
        point = candidate.point
        truth = GroundTruth(fired=point.fired, side=point.side, stop_price=point.stop_price)
        tick = RESEARCH_TICK_SIZE.get(candidate.instrument, Decimal("0.25"))
        score = score_decision(truth, answer, tick_size=tick)
        self.records.append(DecisionRecord(candidate=candidate, answer=answer, score=score))
        self.cursor += 1
        return score

    def tally(self):
        triples = [(GroundTruth(fired=r.candidate.point.fired, side=r.candidate.point.side,
                                 stop_price=r.candidate.point.stop_price), r.answer, r.score)
                   for r in self.records]
        return tally_session(triples)


def build_queue(
    ensemble: Ensemble,
    *,
    start: date,
    end: date,
    n_decisions: int,
    regime_label: str | None,
    previously_wrong: set[DecisionKey],
    rng: random.Random,
    adjustment: str = "back_adjusted",
) -> list[Candidate]:
    """Load bars for every (member, instrument) pair over [start, end],
    extract decision points, filter to `regime_label` if given, build the
    weighted candidate pool, and sample a queue of size n_decisions.
    Raises research.data.loader.HoldoutAccessError, uncaught, if the range
    touches the sealed holdout — the caller (the API route) lets it
    surface as a 4xx rather than degrading the request silently.
    """
    candidates_by_day: dict[tuple[str, str, date], list[Candidate]] = {}
    bars_cache: dict[str, list[Bar]] = {}

    for member in ensemble.members:
        for instrument in member.ir_doc["instruments"]:
            allowed_dates = (
                session_dates_for_label(instrument, regime_label)
                if regime_label is not None else None
            )
            if instrument not in bars_cache:
                frame = load_bars(instrument, start, end, adjustment)
                bars_cache[instrument] = bars_from_frame(frame, instrument)
            bars = bars_cache[instrument]
            for point in extract_decision_points(member.ir_doc, instrument, bars):
                session_date = point.ts.astimezone(ZoneInfo(ET_TZ)).date()
                if allowed_dates is not None and session_date not in allowed_dates:
                    continue
                key = (member.hypothesis_id, instrument, session_date)
                candidates_by_day.setdefault(key, []).append(Candidate(
                    hypothesis_id=member.hypothesis_id, instrument=instrument,
                    regime_label=regime_label, point=point,
                ))

    pool = build_candidate_pool(candidates_by_day, rng=rng)
    return sample_queue(pool, n_decisions, previously_wrong=previously_wrong, rng=rng)


def start_session(ensemble_id: str, queue: list[Candidate]) -> TrainerSession:
    return TrainerSession(session_id=str(uuid.uuid4()), ensemble_id=ensemble_id, queue=queue)
