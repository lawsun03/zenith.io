#!/usr/bin/env python3
"""Run one unattended cycle of the autonomous hypothesis loop
(docs/research-loop/README.md's four-stage cycle; PHASE-PROMPTS.md phase 5).

Loads the ranked, labelled anomaly table (research/anomaly/pipeline.py's
output, built by scripts/run_anomaly_pass.py), triages a shortlist,
drafts a falsifiable hypothesis per shortlisted anomaly, adversarially
reviews each one, enumerates variants around survivors, and writes every
resulting candidate or rejection to the research ledger.

Requires OPENAI_API_KEY and MOONSHOT_API_KEY (read from the environment,
or a .env file at the repo root) — the default stage wiring
(research/loop/config.py) uses only Astra and Kimi K3. XAI_API_KEY is only
needed if that wiring is changed to route a stage through Grok.

Usage:
    python scripts/run_hypothesis_loop.py
    python scripts/run_hypothesis_loop.py --max-shortlist 3 --max-variants 8
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research.anomaly.features import FEATURE_COLUMNS  # noqa: E402
from research.anomaly.paths import ANOMALY_TABLE_PATH  # noqa: E402
from research.anomaly.pipeline import load_table  # noqa: E402
from research.data.folds import load_folds  # noqa: E402
from research.data.loader import load_bars  # noqa: E402
from research.ir.sizing import load_account_config  # noqa: E402
from research.ledger.db import get_connection  # noqa: E402
from research.ledger.paths import LEDGER_DB_PATH  # noqa: E402
from research.loop.config import (  # noqa: E402
    MODEL_PROVIDER,
    PROVIDER_API_KEY_ENV,
    STAGE_ADVERSARIAL_REVIEW,
    STAGE_HYPOTHESIS,
    STAGE_MODELS,
    STAGE_TEMPERATURE,
    STAGE_TRIAGE,
    STAGE_VARIANT_ENUMERATION,
    make_stage_chat_fn,
)
from research.loop.cycle import run_cycle  # noqa: E402
from research.stats.budget import ANNUAL_HYPOTHESIS_CAP  # noqa: E402
from research.trainer.decision_points import bars_from_frame  # noqa: E402

log = logging.getLogger("run_hypothesis_loop")


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


def _data_range() -> str:
    folds = load_folds()
    end = folds.holdout_start - timedelta(days=1)
    return f"{folds.corpus_start.isoformat()}/{end.isoformat()}"


def _load_bars_by_instrument() -> dict[str, list]:
    """NQ/ES/GC bars over the corpus, excluding the holdout — every
    candidate this cycle backtests trades all three pooled (CLAUDE.md
    domain invariant 2), so they're loaded once up front, not per
    candidate. load_bars's own holdout guard rejects a range whose end
    reaches folds.holdout_start at all (research.data.loader.
    _overlaps_holdout: `end >= holdout_start`), so this stops one day
    short of it — matching _data_range's own end date exactly."""
    folds = load_folds()
    end = folds.holdout_start - timedelta(days=1)
    return {
        root: bars_from_frame(load_bars(root, folds.corpus_start, end, "back_adjusted"), root)
        for root in ("NQ", "ES", "GC")
    }


def _collect_api_keys() -> dict[str, str]:
    required_providers = {MODEL_PROVIDER[model] for model in set(STAGE_MODELS.values())}
    api_keys: dict[str, str] = {}
    missing = []
    for provider in required_providers:
        env_var = PROVIDER_API_KEY_ENV[provider]
        key = os.environ.get(env_var)
        if not key:
            missing.append(env_var)
        else:
            api_keys[provider] = key
    if missing:
        print(
            f"missing required API key(s): {', '.join(missing)} (checked environment and .env)",
            file=sys.stderr,
        )
        sys.exit(1)
    return api_keys


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--max-shortlist", type=int, default=5)
    parser.add_argument("--max-variants", type=int, default=12)
    parser.add_argument("--cap", type=int, default=ANNUAL_HYPOTHESIS_CAP)
    parser.add_argument("--table-path", default=str(ANOMALY_TABLE_PATH))
    parser.add_argument("--ledger-path", default=str(LEDGER_DB_PATH))
    args = parser.parse_args()

    _load_env_file(REPO_ROOT / ".env")
    api_keys = _collect_api_keys()

    table = load_table(Path(args.table_path))
    if table is None or table.is_empty():
        log.error(
            "no anomaly table at %s — run scripts/run_anomaly_pass.py first", args.table_path
        )
        sys.exit(1)
    anomaly_rows = table.to_dicts()

    log.info("loading NQ/ES/GC corpus bars (excluding the holdout) — this is the slow part")
    bars_by_instrument = _load_bars_by_instrument()
    account = load_account_config("topstep-50k")

    conn = get_connection(Path(args.ledger_path))
    try:
        result = run_cycle(
            conn,
            anomaly_rows,
            feature_columns=FEATURE_COLUMNS,
            triage_chat_fn=make_stage_chat_fn(STAGE_TRIAGE, api_keys),
            hypothesis_chat_fn=make_stage_chat_fn(STAGE_HYPOTHESIS, api_keys),
            review_chat_fn=make_stage_chat_fn(STAGE_ADVERSARIAL_REVIEW, api_keys),
            variants_chat_fn=make_stage_chat_fn(STAGE_VARIANT_ENUMERATION, api_keys),
            hypothesis_model_name=STAGE_MODELS[STAGE_HYPOTHESIS],
            hypothesis_temperature=STAGE_TEMPERATURE[STAGE_HYPOTHESIS],
            data_range=_data_range(),
            bars_by_instrument=bars_by_instrument,
            account=account,
            max_shortlist=args.max_shortlist,
            max_variants=args.max_variants,
            cap=args.cap,
        )
    finally:
        conn.close()

    log.info(
        "cycle done: %d triaged, %d hypotheses logged (%d survived review, %d rejected "
        "at review, %d generation/review failures), %d gate-evaluated (%d cleared every gate, "
        "%d gate-evaluation failures)",
        result.n_triaged, len(result.hypothesis_ids), result.n_survived_review,
        result.n_review_rejected, result.n_generation_failed, result.n_gate_evaluated,
        result.n_cleared_all_gates, result.n_gate_evaluation_failed,
    )
    if result.budget_exhausted:
        log.error(
            "annual hypothesis cap (%d) reached — loop stopped, this is intended behaviour",
            args.cap,
        )


if __name__ == "__main__":
    main()
