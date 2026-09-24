"""Stage a — Kimi K3 triages the ranked, labelled anomaly table
(docs/research-loop/README.md's four-stage cycle) down to a shortlist
worth reasoning about.

`anomaly_id` is synthesised here as "{instrument}:{session_date}" — the
anomaly table (research.anomaly.pipeline) has no id column of its own,
and this is exactly the string research/ledger's `origin_anomaly_id`
records, so a shortlisted session's provenance stays traceable end to end.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Sequence

from research.loop.prompts import PromptTemplate, load_template
from research.loop.providers import ChatFn

log = logging.getLogger(__name__)

TRIAGE_TEMPLATE = load_template("triage_v1.md")


class TriageParseError(RuntimeError):
    """The triage response wasn't valid JSON or was missing `shortlist`.

    Recoverable at the cycle level: an unparsable triage response means no
    shortlist this cycle, not a crash (CLAUDE.md rule 12 — surfaced via the
    exception, not silently treated as an empty shortlist).
    """


@dataclass(frozen=True)
class TriagedAnomaly:
    anomaly_id: str
    instrument: str
    session_date: str
    regime_label: str | None
    regime_label_confidence: float | None
    rationale: str


@dataclass(frozen=True)
class TriageResult:
    shortlist: tuple[TriagedAnomaly, ...]
    model_version: str
    prompt_hash: str


def _anomaly_id(instrument: str, session_date: object) -> str:
    return f"{instrument}:{session_date}"


def _session_summary(row: dict, feature_columns: Sequence[str]) -> dict:
    return {
        "anomaly_id": _anomaly_id(row["instrument"], row["session_date"]),
        "instrument": row["instrument"],
        "session_date": str(row["session_date"]),
        "regime_label": row.get("regime_label"),
        "regime_label_confidence": row.get("regime_label_confidence"),
        "regime_description": row.get("regime_description"),
        "anomaly_score": row.get("anomaly_score"),
        "features": {c: row[c] for c in feature_columns if row.get(c) is not None},
    }


def triage_anomalies(
    chat_fn: ChatFn,
    anomaly_rows: list[dict],
    *,
    feature_columns: Sequence[str],
    max_shortlist: int = 5,
    template: PromptTemplate = TRIAGE_TEMPLATE,
) -> TriageResult:
    """anomaly_rows is the ranked/labelled anomaly table as row dicts
    (pl.DataFrame.to_dicts()) — this stage never touches Polars directly,
    keeping it testable with plain dicts and independent of the table's
    exact column set beyond `feature_columns`.
    """
    if not anomaly_rows:
        return TriageResult(shortlist=(), model_version="", prompt_hash=template.sha256)

    sessions = [_session_summary(r, feature_columns) for r in anomaly_rows]
    by_id = {s["anomaly_id"]: s for s in sessions}
    prompt = template.render(
        max_shortlist=max_shortlist, sessions_json=json.dumps(sessions, default=str)
    )

    response = chat_fn(prompt)
    try:
        parsed = json.loads(response.text)
        raw_shortlist = parsed["shortlist"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise TriageParseError(f"unparsable triage response: {response.text!r}") from exc

    shortlist = []
    for item in raw_shortlist[:max_shortlist]:
        anomaly_id = item.get("anomaly_id")
        session = by_id.get(anomaly_id)
        if session is None:
            log.warning("triage referenced unknown anomaly_id %r — dropped", anomaly_id)
            continue
        shortlist.append(
            TriagedAnomaly(
                anomaly_id=anomaly_id,
                instrument=session["instrument"],
                session_date=session["session_date"],
                regime_label=session["regime_label"],
                regime_label_confidence=session.get("regime_label_confidence"),
                rationale=str(item.get("rationale", "")),
            )
        )

    log.info(
        "triage: %d/%d anomalies shortlisted (model=%s prompt_hash=%s)",
        len(shortlist), len(anomaly_rows), response.model_version, template.sha256,
    )
    return TriageResult(
        shortlist=tuple(shortlist), model_version=response.model_version, prompt_hash=template.sha256,
    )
