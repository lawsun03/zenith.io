"""xAI Grok client for regime labelling.

Dependency-injected exactly like research.data.ingest's FetchFn: `LabelFn`
is the seam tests inject a fake across, and `make_xai_label_fn` is the one
real adapter — never exercised by tests, the same way
research.data.ingest.make_databento_fetch_fn isn't, because a real call
costs money against the $150 cap (research.anomaly.spend) and needs a
funded XAI_API_KEY.

CLAUDE.md rule 5 — "use the model only for judgment calls" — is exactly
what this is: Grok classifies what kind of day a session was, using search
it's uniquely positioned to run. It never touches price arithmetic, gate
thresholds, or anything the pipeline could compute deterministically.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from research.anomaly.labels import RegimeLabel, parse_label_response

log = logging.getLogger(__name__)

XAI_CHAT_COMPLETIONS_URL = "https://api.x.ai/v1/chat/completions"
XAI_MODEL = "grok-4.7"

# xAI's Live Search billing is per source consulted, not per call. This
# constant is a conservative placeholder for when the API response doesn't
# report actual usage — verify it against xAI's current Live Search
# pricing before trusting cumulative spend, the same caveat
# docs/research-loop/cost-model.md makes about broker fee schedules.
_FALLBACK_COST_PER_CALL_USD = 0.25


class LabelFetchError(RuntimeError):
    """A single session's label call failed or returned something that
    couldn't be parsed. Recoverable at the pipeline level: the session is
    logged and skipped, not defaulted to a category it was never told.
    """


@dataclass(frozen=True)
class LabelResult:
    label: RegimeLabel
    cost_usd: float


# (instrument, session_date, feature_summary) -> LabelResult
LabelFn = Callable[[str, date, dict[str, float]], LabelResult]


def build_prompt(instrument: str, session_date: date, feature_summary: dict[str, float]) -> str:
    features_str = ", ".join(f"{k}={v:.3f}" for k, v in sorted(feature_summary.items()))
    return (
        f"On {session_date.isoformat()}, the {instrument} futures session was flagged as "
        f"statistically unusual versus its own recent history (features: {features_str}). "
        "Use X search and web search to find out what happened that day. "
        "Respond with ONLY a JSON object of the form "
        '{"category": <one of "scheduled_macro", "quarterly_event", "idiosyncratic_shock", '
        '"none_identified">, "description": <one sentence, what happened>, '
        '"confidence": <0.0-1.0>, "sources": [<urls or citations>]}. '
        "scheduled_macro = a known scheduled release (CPI, PPI, NFP, FOMC, PCE). "
        "quarterly_event = a calendar event like options expiry, futures roll, or index "
        "rebalance. idiosyncratic_shock = an unscheduled event (geopolitical, credit, "
        "liquidity, exchange circuit breaker, etc). none_identified = you found no "
        "credible explanation — do not guess."
    )


def make_xai_label_fn(api_key: str) -> LabelFn:
    """Adapter to the real xAI chat-completions API with Live Search
    enabled. Not exercised by tests — those inject a fake LabelFn.
    """
    import requests

    def _label(instrument: str, session_date: date, feature_summary: dict[str, float]) -> LabelResult:
        payload: dict[str, Any] = {
            "model": XAI_MODEL,
            "messages": [
                {"role": "user", "content": build_prompt(instrument, session_date, feature_summary)}
            ],
            "search_parameters": {
                "mode": "on",
                "sources": [{"type": "web"}, {"type": "x"}],
            },
            "response_format": {"type": "json_object"},
        }
        try:
            resp = requests.post(
                XAI_CHAT_COMPLETIONS_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
            label = parse_label_response(json.loads(content))
        except Exception as exc:  # noqa: BLE001 — any failure here is a per-session skip, not a crash
            raise LabelFetchError(f"{instrument} {session_date}: {exc}") from exc

        usage = body.get("usage", {})
        num_sources = usage.get("num_sources_used")
        cost_usd = usage.get("cost_usd")
        if cost_usd is None:
            cost_usd = (
                num_sources * 0.025 if num_sources is not None else _FALLBACK_COST_PER_CALL_USD
            )
            log.warning(
                "xAI response for %s %s carried no usage.cost_usd — using estimate $%.4f; "
                "verify against xAI's current Live Search pricing",
                instrument,
                session_date,
                cost_usd,
            )
        return LabelResult(label=label, cost_usd=float(cost_usd))

    return _label
