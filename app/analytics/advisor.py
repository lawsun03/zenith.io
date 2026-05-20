from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import anthropic

from app.analytics import tools as _tools

_TOOL_DEFS: list[dict] = [
    {
        "name": "get_performance_summary",
        "description": (
            "Overall live-trading performance across all sessions: total trades, "
            "win rate, net P&L, profit factor, expectancy, avg winner/loser, max drawdown."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_killzone_breakdown",
        "description": (
            "Per-killzone breakdown: trade count, win rate, net P&L, avg P&L. "
            "Killzones: london, ny_am, ny_pm, london_ny, asia."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_recent_trades",
        "description": "Most recent closed trades: timestamp, side, killzone, P&L.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of trades to return. Default 50.",
                    "default": 50,
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_backtest_runs",
        "description": (
            "Top historical backtest runs. Each includes params (r_multiple, swing_lookback, "
            "body_atr_multiple, stop_buffer, etc.) and metrics (net_pnl, profit_factor, "
            "win_rate, expectancy, per-killzone breakdown)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sort_by": {
                    "type": "string",
                    "enum": ["profit_factor", "net_pnl", "win_rate", "expectancy"],
                    "description": "Metric to rank by. Default profit_factor.",
                    "default": "profit_factor",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of results. Default 10.",
                    "default": 10,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_current_config",
        "description": "Current bot_config.json: instrument, strategy params, killzones, contracts, entry mode.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_log_summary",
        "description": (
            "Recent log summary: VP filter rejection count, signal denial count, "
            "error/warning counts, with example messages."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Days of logs to include. Default 2.",
                    "default": 2,
                }
            },
            "required": [],
        },
    },
]

_TOOL_FN_MAP = {
    "get_performance_summary": _tools.get_performance_summary,
    "get_killzone_breakdown":  _tools.get_killzone_breakdown,
    "get_recent_trades":       _tools.get_recent_trades,
    "get_backtest_runs":       _tools.get_backtest_runs,
    "get_current_config":      _tools.get_current_config,
    "get_log_summary":         _tools.get_log_summary,
}

_SYSTEM = (
    "You are a trading strategy advisor for a TopstepX Micro Gold (MGC) futures bot. "
    "The strategy uses sweep + displacement + Fair Value Gap (FVG) signals, active only "
    "during configured kill zones (london, ny_am, ny_pm, london_ny, asia). "
    "Use the available tools to investigate live performance and backtest history before "
    "drawing conclusions. "
    "Provide specific, actionable recommendations — reference exact bot_config.json param "
    "names and concrete numbers from the data. "
    "Limit your final response to 5 bullet points or fewer."
)


async def run_advisor(question: str | None = None) -> AsyncIterator[str]:
    """
    Agentic loop. Yields SSE-formatted strings.
    Caps at 10 tool rounds to bound API spend (~$0.10–0.20 per session).
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        yield _sse("error", {"message": "ANTHROPIC_API_KEY not set in .env"})
        return

    client = anthropic.AsyncAnthropic(api_key=api_key)
    user_msg = question or (
        "Analyze my bot's live performance and backtest history. "
        "Recommend specific parameter changes to improve profitability."
    )
    messages: list[dict] = [{"role": "user", "content": user_msg}]

    for _ in range(10):
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=_SYSTEM,
            tools=_TOOL_DEFS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    yield _sse("text_delta", {"delta": block.text})
            yield _sse("done", {})
            return

        # stop_reason == "tool_use": execute all tool calls then loop
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            yield _sse("tool_call", {"name": block.name, "input": block.input})
            fn = _TOOL_FN_MAP.get(block.name)
            try:
                kwargs = block.input if isinstance(block.input, dict) else {}
                result = fn(**kwargs) if fn else {"error": f"unknown tool: {block.name}"}
            except Exception as exc:
                result = {"error": str(exc)}
            yield _sse("tool_result", {"name": block.name, "preview": json.dumps(result)[:300]})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })

        if not tool_results:
            yield _sse("done", {})
            return
        messages.append({"role": "user", "content": tool_results})

    yield _sse("error", {"message": "Exceeded 10 tool rounds — partial results shown above."})


def _sse(event_type: str, data: dict) -> str:
    return f"data: {json.dumps({'type': event_type, **data})}\n\n"
