#!/usr/bin/env python3
"""One minimal, real API call to each of the three research-loop model
providers (xAI, OpenAI, Moonshot) — a connectivity/credentials/shape
check, not a functional test of any pipeline logic.

Writes NOTHING to the ledger or the anomaly table: no SpendLedger, no
research.anomaly.pipeline, no research.ledger — just a bare HTTP request
per provider, printing HTTP status, model, and (where the provider
reports one) real dollar cost. Each call is the smallest prompt that
still exercises the real endpoint/shape this project depends on:
  - xAI:      POST /v1/responses with tools: [web_search, x_search] —
              the exact shape research.anomaly.grok_client.make_xai_label_fn
              uses, so a shape regression here would also break that.
  - OpenAI:   POST /v1/chat/completions — research.loop.providers.
              make_openai_chat_fn's shape.
  - Moonshot: POST /v1/chat/completions — research.loop.providers.
              make_moonshot_chat_fn's shape.

Requires XAI_API_KEY, OPENAI_API_KEY and MOONSHOT_API_KEY (environment or
.env). Each provider is tried independently — one failing doesn't stop
the others — and the script exits non-zero if any of the three failed.

Usage:
    python scripts/smoke_test_providers.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research.anomaly.grok_client import XAI_MODEL, XAI_RESPONSES_URL, _cost_from_usage  # noqa: E402
from research.loop.providers import (  # noqa: E402
    ASTRA_MODEL,
    KIMI_MODEL,
    MOONSHOT_CHAT_COMPLETIONS_URL,
    OPENAI_CHAT_COMPLETIONS_URL,
)


def _load_env_file(path: Path) -> None:
    """Minimal KEY=VALUE .env loader — matches scripts/run_anomaly_pass.py."""
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _print_result(provider: str, *, status: int | str, model: str | None, cost_usd: float | None, note: str = "") -> bool:
    ok = isinstance(status, int) and 200 <= status < 300
    marker = "OK" if ok else "FAIL"
    cost_str = f"${cost_usd:.6f}" if cost_usd is not None else "n/a"
    print(f"[{marker}] {provider:10s} status={status} model={model or 'n/a'} cost={cost_str} {note}")
    return ok


def smoke_test_xai(api_key: str) -> bool:
    import requests

    payload = {
        "model": XAI_MODEL,
        "input": [{"role": "user", "content": "Reply with the single word: pong"}],
        "tools": [{"type": "web_search"}, {"type": "x_search"}],
    }
    try:
        resp = requests.post(
            XAI_RESPONSES_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload, timeout=60,
        )
    except requests.RequestException as exc:
        return _print_result("xai", status="request-error", model=XAI_MODEL, cost_usd=None, note=str(exc))

    if not resp.ok:
        return _print_result("xai", status=resp.status_code, model=XAI_MODEL, cost_usd=None,
                              note=f"body: {resp.text[:300]}")

    body = resp.json()
    usage = body.get("usage", {})
    cost_usd, was_estimated = _cost_from_usage(usage)
    note = "(cost estimated, no cost_in_usd_ticks in response)" if was_estimated else ""
    return _print_result("xai", status=resp.status_code, model=body.get("model", XAI_MODEL),
                          cost_usd=cost_usd, note=note)


def smoke_test_openai(api_key: str) -> bool:
    import requests

    payload = {
        "model": ASTRA_MODEL,
        "messages": [{"role": "user", "content": "Reply with the single word: pong"}],
        "temperature": 0.0,
    }
    try:
        resp = requests.post(
            OPENAI_CHAT_COMPLETIONS_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload, timeout=60,
        )
    except requests.RequestException as exc:
        return _print_result("openai", status="request-error", model=ASTRA_MODEL, cost_usd=None, note=str(exc))

    if not resp.ok:
        return _print_result("openai", status=resp.status_code, model=ASTRA_MODEL, cost_usd=None,
                              note=f"body: {resp.text[:300]}")

    body = resp.json()
    usage = body.get("usage", {})
    note = f"usage={json.dumps(usage)}" if usage else "(no usage in response)"
    return _print_result("openai", status=resp.status_code, model=body.get("model", ASTRA_MODEL),
                          cost_usd=None, note=note)


def smoke_test_moonshot(api_key: str) -> bool:
    import requests

    payload = {
        "model": KIMI_MODEL,
        "messages": [{"role": "user", "content": "Reply with the single word: pong"}],
        "temperature": 0.0,
    }
    try:
        resp = requests.post(
            MOONSHOT_CHAT_COMPLETIONS_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload, timeout=60,
        )
    except requests.RequestException as exc:
        return _print_result("moonshot", status="request-error", model=KIMI_MODEL, cost_usd=None, note=str(exc))

    if not resp.ok:
        return _print_result("moonshot", status=resp.status_code, model=KIMI_MODEL, cost_usd=None,
                              note=f"body: {resp.text[:300]}")

    body = resp.json()
    usage = body.get("usage", {})
    note = f"usage={json.dumps(usage)}" if usage else "(no usage in response)"
    return _print_result("moonshot", status=resp.status_code, model=body.get("model", KIMI_MODEL),
                          cost_usd=None, note=note)


def main() -> None:
    _load_env_file(REPO_ROOT / ".env")

    checks = [
        ("XAI_API_KEY", smoke_test_xai),
        ("OPENAI_API_KEY", smoke_test_openai),
        ("MOONSHOT_API_KEY", smoke_test_moonshot),
    ]

    results = []
    for env_var, fn in checks:
        api_key = os.environ.get(env_var)
        if not api_key:
            print(f"[SKIP] {env_var} not set (checked environment and .env)")
            results.append(False)
            continue
        try:
            results.append(fn(api_key))
        except Exception as exc:  # noqa: BLE001 — a smoke test must report, never crash uninformatively
            print(f"[FAIL] {env_var}: unexpected error: {exc}")
            results.append(False)

    if not all(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
