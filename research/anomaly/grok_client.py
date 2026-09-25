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

MIGRATION (2026-09): xAI removed the Live Search API
(`search_parameters` on /v1/chat/completions) on 2026-01-12 — every call
through it now returns 410 Gone, which is why every label was failing.
This module now targets the Responses API (POST /v1/responses), input
instead of messages, tools: [{"type": "web_search"}, {"type": "x_search"}]
instead of search_parameters (docs.x.ai/developers/tools/web-search).
Response shape, verified against docs.x.ai/developers/tools/citations and
docs.x.ai/developers/cost-tracking (both first-party, fetched live while
writing this):
  - output text:  output[].content[] where content.type == "output_text",
                   text is content.text
  - citations:    that same content block's `annotations` list, each
                   {"type": "url_citation", "url": ..., "title": ..., ...}
  - real cost:    usage.cost_in_usd_ticks — xAI's own docs: "every REST
                   completion and response" includes it, 1 USD = 1e10
                   ticks, "the actual amount billed ... inclusive of all
                   token costs and server-side tool invocation costs" —
                   this is the one number here that's authoritative, not
                   an estimate, so it's used whenever present.
Neither docs.x.ai page that was reachable showed a full example response
body with every field (citations page showed only the annotations
excerpt), so parsing below is deliberately tolerant of the response
shape sitting a little differently than expected — but the primary path
matches what's documented, and any request that returns a shape none of
this expects is a hard failure (rule 12), never a silently-wrong parse.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from research.anomaly.labels import RegimeLabel, parse_label_response

log = logging.getLogger(__name__)

XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"
XAI_MODEL = "grok-4.7"

# docs.x.ai/developers/pricing, read live while writing this migration
# (2026-09) — grok-4.7, < 200k context: $2.00 / 1M input tokens,
# $6.00 / 1M output tokens. Used ONLY when the response genuinely carries
# no usage.cost_in_usd_ticks (see module docstring) — that field is the
# real, authoritative, already-tool-cost-inclusive figure and is always
# preferred when present.
_INPUT_COST_PER_TOKEN_USD = 2.00 / 1_000_000
_OUTPUT_COST_PER_TOKEN_USD = 6.00 / 1_000_000
# docs.x.ai/developers/pricing: web_search and x_search are each
# $5 / 1,000 calls when billed per call (x_search has separate per-post/
# per-profile meters for some usage; $5/1k calls is the closest single
# number available for a rough estimate here).
_TOOL_CALL_COST_USD = 5.00 / 1_000
_USD_TICKS_PER_DOLLAR = 10_000_000_000

# Statuses that will never succeed on retry — abort the whole pass
# immediately rather than skip-and-continue (an expired/wrong key or a
# dead endpoint will fail every remaining call identically).
FATAL_STATUS_CODES = frozenset({401, 403, 404, 410})

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

# A tool-using call (web_search + x_search, an agentic loop xAI runs
# server-side) genuinely takes 45-90+ seconds on success — verified
# against real grok-4.7 calls (2026-09): two calls in a 9-call live batch
# timed out at the previous 90s limit while others succeeded well past
# 60s. 180s gives real search loops headroom without waiting forever on a
# genuinely hung connection.
REQUEST_TIMEOUT_SECONDS = 180

# 429 handling. Several labels are in flight at once (--concurrency), so a
# rate limit is expected traffic, not a failure: back off and retry inside
# the worker instead of letting it count toward the pipeline's
# consecutive-failure abort. Retry-After is honoured when the server sends
# one. A 429 whose body says the account is out of credit is a billing
# problem that no amount of waiting fixes (OpenAI returned exactly that for
# an empty balance) — that one is treated as fatal, not retried.
RATE_LIMIT_MAX_RETRIES = 6
RATE_LIMIT_BASE_DELAY_SECONDS = 2.0
RATE_LIMIT_MAX_DELAY_SECONDS = 60.0
_BILLING_MARKERS = ("insufficient", "credit", "balance", "billing")


class LabelFetchError(RuntimeError):
    """A single session's label call failed or returned something that
    couldn't be parsed. Recoverable at the pipeline level: the session is
    logged and skipped, not defaulted to a category it was never told.

    Carries the HTTP status and a response-body excerpt whenever the
    failure came from an HTTP response, so the pipeline's warning can
    include both (CLAUDE.md rule 12 — the log line, not just this
    exception's str(), is what an operator actually reads).
    """

    def __init__(self, message: str, *, status_code: int | None = None, body_excerpt: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body_excerpt = body_excerpt


class LabelFetchFatalError(LabelFetchError):
    """A status in FATAL_STATUS_CODES — the caller (research.anomaly.
    pipeline.label_top_decile) must abort the whole pass immediately, not
    skip this session and try the next one."""


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


def _extract_output_text_and_citations(body: dict) -> tuple[str, list[str]]:
    """output[].content[] where content.type == "output_text" ->
    (text, [citation urls from that block's annotations]) — of the LAST
    such block, not the first.

    Verified against a live grok-4.7 response (2026-09): a completed,
    tool-using response's `output` array is NOT one message — it's
    reasoning items, an early narration message ("I'll look up..." with
    its own output_text block), the tool-call items, and finally the
    actual answer as a later message. Taking the first output_text block
    silently returns that narration instead of the answer; the last one
    is the model's final synthesized message once every tool call has
    resolved.

    Raises KeyError/IndexError (caller wraps as LabelFetchError) if no
    output_text block exists at all — that is a shape this module doesn't
    understand, and CLAUDE.md rule 12 says that's a failure, not a guess.
    """
    last: tuple[str, list[str]] | None = None
    for item in body["output"]:
        for block in item.get("content", []):
            if block.get("type") == "output_text":
                citations = [
                    a["url"] for a in block.get("annotations", []) if a.get("type") == "url_citation" and a.get("url")
                ]
                last = (block["text"], citations)
    if last is None:
        raise KeyError("no output_text content block in response")
    return last


def _extract_json_object(text: str) -> dict:
    """The prompt asks for ONLY a JSON object, but nothing on the Responses
    API enforces that structurally here (unlike the old response_format
    on /v1/chat/completions) — so this tolerates the model wrapping the
    object in prose or a markdown code fence, same spirit as the citation-
    parsing tolerance above, while still failing loud if there is no JSON
    object in the text at all."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        raise ValueError(f"no JSON object found in response text: {text!r}")
    return json.loads(match.group(0))


def _cost_from_usage(usage: dict) -> tuple[float, bool]:
    """(cost_usd, was_estimated). Prefers usage.cost_in_usd_ticks — xAI's
    own real, tool-cost-inclusive billed amount — over any estimate."""
    ticks = usage.get("cost_in_usd_ticks")
    if ticks is not None:
        return float(ticks) / _USD_TICKS_PER_DOLLAR, False

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    tool_usage = usage.get("server_side_tool_usage") or {}
    tool_calls = sum(v for v in tool_usage.values() if isinstance(v, (int, float)))
    if input_tokens is not None and output_tokens is not None:
        estimate = (
            input_tokens * _INPUT_COST_PER_TOKEN_USD
            + output_tokens * _OUTPUT_COST_PER_TOKEN_USD
            + tool_calls * _TOOL_CALL_COST_USD
        )
        return estimate, True

    return _TOOL_CALL_COST_USD * 2, True  # genuinely nothing to go on — two tool calls' worth, as a floor


def _is_billing_429(resp: Any) -> bool:
    body = (resp.text or "").lower()
    return any(marker in body for marker in _BILLING_MARKERS)


def _post_with_rate_limit_backoff(
    do_post: Callable[[], Any],
    *,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
    max_retries: int = RATE_LIMIT_MAX_RETRIES,
    context: str = "",
) -> Any:
    """Call `do_post()`; on HTTP 429 wait (Retry-After if given, else
    exponential from RATE_LIMIT_BASE_DELAY_SECONDS, capped, plus up to 25%
    jitter so concurrent workers don't retry in lockstep) and try again, up
    to `max_retries` times. Returns the final response either way — the
    caller turns a still-429 into a LabelFetchError. A billing 429 is
    returned immediately, unretried."""
    for attempt in range(max_retries + 1):
        resp = do_post()
        if resp.status_code != 429 or attempt == max_retries or _is_billing_429(resp):
            return resp
        retry_after = resp.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after is not None else None
        except ValueError:
            delay = None
        if delay is None:
            delay = RATE_LIMIT_BASE_DELAY_SECONDS * (2 ** attempt)
            delay += delay * 0.25 * jitter()
        delay = min(delay, RATE_LIMIT_MAX_DELAY_SECONDS)
        log.warning("xAI 429 rate limit for %s — backing off %.1fs (retry %d/%d)",
                    context, delay, attempt + 1, max_retries)
        sleep(delay)
    raise AssertionError("unreachable")


def make_xai_label_fn(api_key: str) -> LabelFn:
    """Adapter to the real xAI Responses API with web_search + x_search
    tools enabled. Not exercised by tests — those inject a fake LabelFn.
    """
    import requests

    def _label(instrument: str, session_date: date, feature_summary: dict[str, float]) -> LabelResult:
        payload: dict[str, Any] = {
            "model": XAI_MODEL,
            "input": [{"role": "user", "content": build_prompt(instrument, session_date, feature_summary)}],
            "tools": [{"type": "web_search"}, {"type": "x_search"}],
        }
        context = f"{instrument} {session_date}"
        try:
            resp = _post_with_rate_limit_backoff(
                lambda: requests.post(
                    XAI_RESPONSES_URL,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                ),
                context=context,
            )
        except requests.RequestException as exc:
            raise LabelFetchError(f"{context}: request failed: {exc}") from exc

        if not resp.ok:
            excerpt = resp.text[:500]
            fatal = resp.status_code in FATAL_STATUS_CODES or (resp.status_code == 429 and _is_billing_429(resp))
            error_cls = LabelFetchFatalError if fatal else LabelFetchError
            raise error_cls(
                f"{context}: HTTP {resp.status_code} from {XAI_RESPONSES_URL} — {excerpt}",
                status_code=resp.status_code, body_excerpt=excerpt,
            )

        body: dict | None = None
        try:
            body = resp.json()
            text, citation_urls = _extract_output_text_and_citations(body)
            parsed = _extract_json_object(text)
            if citation_urls and not parsed.get("sources"):
                parsed = {**parsed, "sources": citation_urls}
            label = parse_label_response(parsed)
        except Exception as exc:  # noqa: BLE001 — a parse failure is a per-session skip, not a crash
            excerpt = json.dumps(body)[:500] if body is not None else resp.text[:500]
            raise LabelFetchError(f"{context}: couldn't parse response: {exc} — body excerpt: {excerpt}") from exc

        usage = body.get("usage", {})
        cost_usd, was_estimated = _cost_from_usage(usage)
        if was_estimated:
            log.warning(
                "xAI response for %s carried no usage.cost_in_usd_ticks — using an estimate "
                "$%.4f from token/tool-call counts; verify against docs.x.ai/developers/pricing",
                context, cost_usd,
            )
        return LabelResult(label=label, cost_usd=cost_usd)

    return _label
