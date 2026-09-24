"""Loop health — the numbers PHASE-PROMPTS.md phase 7 and CLAUDE.md rule 13
both call out as "surfaced unprompted, because they're exactly what stops
getting checked once the system runs smoothly": current PBO, trials
consumed against the annual budget, holdout touches, and a rejection-by-
gate breakdown.

Every number here is read straight off research.ledger.api's existing
functions (trial_count_at, rejections_by_gate, trial_budget_by_year,
holdout_usage, get_loop_pbo) — this module writes no new SQL query, per
the task's own instruction to reuse them.

Deterministic, not model-generated: CLAUDE.md rule 5 is explicit that gate
arithmetic and significance numbers are never a model's call. This text is
built in plain Python and handed to the chat model as a fact, the same way
research.trainer.scoring's fidelity numbers are computed before a trainee
ever sees them — the model's job is to narrate context around real
numbers, not to compute or paraphrase-and-risk-distorting them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from research.ledger.api import (
    get_loop_pbo,
    holdout_usage,
    rejections_by_gate,
    trial_budget_by_year,
    trial_count_at,
)
from research.stats.budget import ANNUAL_HYPOTHESIS_CAP


@dataclass(frozen=True)
class LoopHealth:
    loop_pbo: float | None
    loop_pbo_computed_at: str | None
    trial_count: int
    hypotheses_this_year: int
    annual_cap: int
    holdout_touches_used: int
    holdout_last_touch: str | None
    rejections_by_gate: list[dict[str, Any]]


def loop_health_summary(conn, *, now: datetime | None = None) -> LoopHealth:
    now = now or datetime.now(timezone.utc)
    year = str(now.year)

    pbo_row = get_loop_pbo(conn)
    budget_by_year = {r["year"]: r for r in trial_budget_by_year(conn)}
    this_year = budget_by_year.get(year, {"hypotheses_logged": 0})
    holdout_rows = holdout_usage(conn)
    holdout = holdout_rows[0] if holdout_rows else {"touches_used": 0, "last_touch": None}

    return LoopHealth(
        loop_pbo=pbo_row["loop_pbo"] if pbo_row else None,
        loop_pbo_computed_at=pbo_row["loop_pbo_computed_at"] if pbo_row else None,
        trial_count=trial_count_at(conn, now),
        hypotheses_this_year=this_year.get("hypotheses_logged", 0) or 0,
        annual_cap=ANNUAL_HYPOTHESIS_CAP,
        holdout_touches_used=holdout.get("touches_used", 0) or 0,
        holdout_last_touch=holdout.get("last_touch"),
        rejections_by_gate=rejections_by_gate(conn),
    )


def format_health_summary(health: LoopHealth) -> str:
    """Plain text for the agent's unprompted opening message."""
    lines = ["LOOP HEALTH"]

    if health.loop_pbo is not None:
        flag = "  <- BLOCKS ALL PROMOTION (> 0.5)" if health.loop_pbo > 0.5 else ""
        lines.append(f"- current loop PBO: {health.loop_pbo:.3f} "
                      f"(computed {health.loop_pbo_computed_at}){flag}")
    else:
        lines.append(
            "- current loop PBO: not yet computed — no cycle has gate-evaluated "
            "2+ candidates with enough aligned trading days yet"
        )

    lines.append(
        f"- hypotheses logged this year: {health.hypotheses_this_year}/{health.annual_cap} "
        f"(annual cap — CLAUDE.md rule 6: hitting it stops the loop, that's intended)"
    )
    lines.append(f"- trial count right now (variant-weighted): {health.trial_count}")

    if health.holdout_touches_used:
        lines.append(
            f"- holdout touched {health.holdout_touches_used} time(s), most recently "
            f"{health.holdout_last_touch} — no numeric cap exists; every touch is a one-way door"
        )
    else:
        lines.append("- holdout: never touched")

    if health.rejections_by_gate:
        worst = ", ".join(f"gate {r['gate']} ({r['n']})" for r in health.rejections_by_gate)
        lines.append(f"- rejections by gate, worst first: {worst}")
    else:
        lines.append("- rejections by gate: no candidates have been gate-evaluated yet")

    return "\n".join(lines)
