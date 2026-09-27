"""Strategy IR validator.

Two layers, per CLAUDE.md's instruction to build "a validator for the schema,
including the rules the JSON Schema cannot express":

  1. Structural validation against docs/research-loop/strategy-ir.schema.json.
     Hand-rolled rather than a `jsonschema` dependency — the schema is small,
     fixed, and specific to this project; a generic engine would be more code
     for less clarity (CLAUDE.md rule 2), and `jsonschema` isn't in this
     project's environment.
  2. The three invariants JSON Schema's declarative shape cannot express:
       a. entry composes only from predicates explicitly marked
          `"recognizable": true` (the schema *allows* the field to be
          omitted, which would silently admit a non-recognizable-looking
          node structurally — this closes that gap).
       b. no reference anywhere in the document to regime_label, account
          balance, daily loss limit, or trailing drawdown (CLAUDE.md
          invariants #4 and #5). Defense in depth: today's closed `level`
          enum and `additionalProperties: false` already block these as
          *keys*, but this check is schema-version-independent and catches
          a future loosening immediately.
       c. instruments is exactly one of two pools: the legacy {"NQ", "ES",
          "GC"} (so pre-silver strategies keep backtesting bit-identically)
          or {"NQ", "ES", "GC", "SI"}. Never a subset — per-instrument
          fitting is banned (CLAUDE.md rule 2). Stated explicitly so it
          keeps holding if the schema's cardinality constraints loosen.

validate() returns a list of human-readable error strings; empty = valid.
Never raises on a malformed document — a strategy is data, and a bad
document is a rejected candidate, not a crash (CLAUDE.md rule 12: a gate
that cannot be evaluated is a failure, not a silent pass).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs" / "research-loop" / "strategy-ir.schema.json"
)

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

_LEGACY_POOL = frozenset({"NQ", "ES", "GC"})
_POOL = frozenset({"NQ", "ES", "GC", "SI"})

_LEVELS = {
    "prior_day_high", "prior_day_low",
    "prior_session_high", "prior_session_low",
    "session_open", "midnight_open", "London_open", "NY_open",
    "opening_range_high", "opening_range_low",
    "swing_high", "swing_low",
    "prior_week_high", "prior_week_low",
    "sma_7", "sma_21", "vwap",
}

_RECOGNIZABLE_OPS = {
    "sweep_of", "displacement", "fvg", "close_beyond", "retrace_to",
    "cross_of", "inside_range", "time_window", "consecutive_closes",
}
_ANY_ONLY_OPS = {
    "bars_elapsed", "session_end", "atr_multiple_move", "trailing_stop",
    "rolling_quantile",
}
_COMBINATORS = {"and", "or"}

_STOP_TYPES = {"atr", "realised_sigma", "structural"}
_STOP_ANCHORS = {"swing_low", "swing_high", "sweep_extreme", "entry_bar"}
_TARGET_TYPES = {"atr", "r_multiple", "session_level"}
_SIZING_FAMILIES = {"micro", "mini", "full"}

# Banned regardless of where in the document they'd appear — CLAUDE.md
# invariants #4 (account size never enters rule design) and #5 (regime
# labels are classification only, never a predicate). Matched
# case-insensitively against every string leaf and every dict key.
_BANNED_TERMS = (
    "regime_label", "account_balance", "daily_loss_limit",
    "trailing_drawdown", "trailing_max_loss", "dll", "mll",
)


def load_schema_doc() -> dict:
    return json.loads(_SCHEMA_PATH.read_text())


def validate(doc: dict) -> list[str]:
    """Validate an IR document. Returns a list of errors; empty = valid."""
    errors: list[str] = []
    if not isinstance(doc, dict):
        return ["document must be a JSON object"]

    _check_top_level(doc, errors)
    _check_banned_terms(doc, errors)
    return errors


# ----------------------------------------------------------------------
# Structural validation
# ----------------------------------------------------------------------

def _check_top_level(doc: dict, errors: list[str]) -> None:
    required = ["ir_version", "name", "instruments", "session", "entry",
                "exit", "stop", "sizing"]
    for key in required:
        if key not in doc:
            errors.append(f"missing required field '{key}'")

    allowed = set(required) | {"target", "forecast"}
    for key in doc:
        if key not in allowed:
            errors.append(f"unexpected top-level field '{key}'")

    if doc.get("ir_version") != "1.0":
        errors.append(f"ir_version must be \"1.0\", got {doc.get('ir_version')!r}")

    name = doc.get("name")
    if not isinstance(name, str) or not (3 <= len(name) <= 80):
        errors.append("name must be a string of length 3-80")

    _check_instruments(doc.get("instruments"), errors)
    _check_session(doc.get("session"), errors)

    if "entry" in doc:
        _check_predicate(doc["entry"], errors, path="entry", recognizable_only=True)
    if "exit" in doc:
        _check_predicate(doc["exit"], errors, path="exit", recognizable_only=False)

    _check_stop(doc.get("stop"), errors)
    if "target" in doc:
        _check_target(doc["target"], errors)
    if "forecast" in doc:
        _check_forecast(doc["forecast"], errors)
    _check_sizing(doc.get("sizing"), errors)


def _check_instruments(value: Any, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append("instruments must be an array")
        return
    if len(set(value)) != len(value) or frozenset(value) not in (_LEGACY_POOL, _POOL):
        errors.append(
            "instruments must be exactly [\"NQ\", \"ES\", \"GC\", \"SI\"] "
            "(or the legacy [\"NQ\", \"ES\", \"GC\"]) — pooled "
            "fitting is mandatory, CLAUDE.md rule 2 — got "
            f"{value!r}"
        )


def _check_session(value: Any, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("session must be an object")
        return
    for key in ("start", "end", "tz"):
        if key not in value:
            errors.append(f"session missing '{key}'")
    for key in value:
        if key not in ("start", "end", "tz"):
            errors.append(f"session has unexpected field '{key}'")
    for key in ("start", "end"):
        v = value.get(key)
        if v is not None and not _TIME_RE.match(v):
            errors.append(f"session.{key} must match HH:MM, got {v!r}")
    if "tz" in value and value["tz"] != "America/New_York":
        errors.append('session.tz must be "America/New_York"')


def _check_predicate(node: Any, errors: list[str], *, path: str,
                      recognizable_only: bool) -> None:
    """Recursively validate a predicate node. recognizable_only=True means
    every leaf under this node (entry) must carry recognizable: true."""
    if not isinstance(node, dict):
        errors.append(f"{path}: predicate must be an object")
        return
    op = node.get("op")
    if op in _COMBINATORS:
        operands = node.get("operands")
        if not isinstance(operands, list) or not (2 <= len(operands) <= 4):
            errors.append(f"{path}: {op} needs 2-4 operands")
            return
        extra = set(node) - {"op", "operands"}
        if extra:
            errors.append(f"{path}: {op} has unexpected field(s) {sorted(extra)}")
        for i, sub in enumerate(operands):
            _check_predicate(sub, errors, path=f"{path}.operands[{i}]",
                              recognizable_only=recognizable_only)
        return

    valid_ops = _RECOGNIZABLE_OPS | (_ANY_ONLY_OPS if not recognizable_only else set())
    if op not in valid_ops:
        errors.append(f"{path}: unknown or disallowed op {op!r}")
        return

    if recognizable_only and node.get("recognizable") is not True:
        errors.append(
            f"{path}: entry predicate op={op!r} must set \"recognizable\": "
            "true — entry composes only from recognizable predicates "
            "(CLAUDE.md rule 1 / gate 9)"
        )
    if not recognizable_only and op in _ANY_ONLY_OPS and node.get("recognizable") is not False:
        errors.append(
            f"{path}: op={op!r} is a non-recognizable predicate and must "
            "set \"recognizable\": false"
        )

    allowed_fields = {
        "op", "level", "level_b", "direction", "min_atr", "min_offset", "pct", "n",
        "within_bars", "start", "end", "recognizable",
        "multiple", "quantile", "lookback",
    }
    extra = set(node) - allowed_fields
    if extra:
        errors.append(f"{path}: op={op!r} has unexpected field(s) {sorted(extra)}")

    for level_field in ("level", "level_b"):
        if level_field in node and node[level_field] not in _LEVELS:
            errors.append(f"{path}: {level_field}={node[level_field]!r} not a known level")

    if "min_atr" in node and "min_offset" in node:
        errors.append(
            f"{path}: op={op!r} sets both min_atr and min_offset — exactly one "
            "penetration/threshold basis must be chosen"
        )
    if "min_offset" in node:
        v = node["min_offset"]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
            errors.append(f"{path}: min_offset must be a non-negative number")

    if "direction" in node and node["direction"] not in ("up", "down", "either"):
        errors.append(f"{path}: direction must be up/down/either")


def _check_stop(value: Any, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("stop must be an object")
        return
    for key in ("type", "multiple", "lookback"):
        if key not in value:
            errors.append(f"stop missing '{key}'")
    extra = set(value) - {"type", "multiple", "lookback", "anchor", "buffer"}
    if extra:
        errors.append(f"stop has unexpected field(s) {sorted(extra)}")
    if value.get("type") not in _STOP_TYPES:
        errors.append(f"stop.type must be one of {sorted(_STOP_TYPES)}")
    multiple = value.get("multiple")
    if isinstance(multiple, (int, float)) and not (0.25 <= multiple <= 5.0):
        errors.append("stop.multiple must be in [0.25, 5.0]")
    lookback = value.get("lookback")
    if isinstance(lookback, int) and not (5 <= lookback <= 100):
        errors.append("stop.lookback must be in [5, 100]")
    if "anchor" in value and value["anchor"] not in _STOP_ANCHORS:
        errors.append(f"stop.anchor must be one of {sorted(_STOP_ANCHORS)}")
    if "buffer" in value:
        buf = value["buffer"]
        if not isinstance(buf, (int, float)) or isinstance(buf, bool) or buf < 0:
            errors.append("stop.buffer must be a non-negative number")


def _check_target(value: Any, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("target must be an object")
        return
    for key in ("type", "multiple"):
        if key not in value:
            errors.append(f"target missing '{key}'")
    extra = set(value) - {"type", "multiple"}
    if extra:
        errors.append(f"target has unexpected field(s) {sorted(extra)}")
    if value.get("type") not in _TARGET_TYPES:
        errors.append(f"target.type must be one of {sorted(_TARGET_TYPES)}")
    multiple = value.get("multiple")
    if isinstance(multiple, (int, float)) and not (0.5 <= multiple <= 10.0):
        errors.append("target.multiple must be in [0.5, 10.0]")


def _check_forecast(value: Any, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("forecast must be an object")
        return
    extra = set(value) - {"scaled", "cap"}
    if extra:
        errors.append(f"forecast has unexpected field(s) {sorted(extra)}")
    if "cap" in value and value["cap"] != 20:
        errors.append("forecast.cap must be 20")


def _check_sizing(value: Any, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("sizing must be an object")
        return
    for key in ("family", "vol_target_annual"):
        if key not in value:
            errors.append(f"sizing missing '{key}'")
    extra = set(value) - {"family", "vol_target_annual", "max_contracts"}
    if extra:
        errors.append(f"sizing has unexpected field(s) {sorted(extra)}")
    if "family" in value and value["family"] not in _SIZING_FAMILIES:
        errors.append(f"sizing.family must be one of {sorted(_SIZING_FAMILIES)}")
    vta = value.get("vol_target_annual")
    if isinstance(vta, (int, float)) and not (0.05 <= vta <= 0.40):
        errors.append("sizing.vol_target_annual must be in [0.05, 0.40]")
    max_contracts = value.get("max_contracts")
    if isinstance(max_contracts, int) and not (1 <= max_contracts <= 50):
        errors.append("sizing.max_contracts must be in [1, 50]")


# ----------------------------------------------------------------------
# Banned-term scan (defense in depth beyond the schema's closed vocab)
# ----------------------------------------------------------------------

def _check_banned_terms(doc: Any, errors: list[str], path: str = "$") -> None:
    if isinstance(doc, dict):
        for k, v in doc.items():
            if _mentions_banned(k):
                errors.append(f"{path}: banned term in key {k!r}")
            _check_banned_terms(v, errors, f"{path}.{k}")
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            _check_banned_terms(v, errors, f"{path}[{i}]")
    elif isinstance(doc, str):
        if _mentions_banned(doc):
            errors.append(f"{path}: banned term in value {doc!r}")


def _mentions_banned(s: str) -> bool:
    lowered = s.lower()
    return any(term in lowered for term in _BANNED_TERMS)
