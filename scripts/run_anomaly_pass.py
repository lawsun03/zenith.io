#!/usr/bin/env python3
"""Run the anomaly detection + Grok regime-labelling pass.

Recomputes the ranked anomaly table for NQ, ES and GC from the accessible
corpus (never the holdout — research.data.loader enforces that), carries
forward any regime labels already on disk, and asks Grok to label whatever
is newly in the top ~10% and not yet labelled. This is both the one-time
backfill and the nightly incremental job (docs/research-loop/README.md
phase 3) — the only thing that's actually incremental between runs is
Grok labelling; the feature/ranking table is cheap enough to recompute in
full every time (research/anomaly/pipeline.py).

Requires XAI_API_KEY (read from the environment, or a .env file at the
repo root in KEY=VALUE form) unless --no-label is passed, in which case
the table is rebuilt and re-ranked but no Grok calls are made and no new
labels are requested.

Usage:
    python scripts/run_anomaly_pass.py                  # normal nightly/backfill run
    python scripts/run_anomaly_pass.py --no-label        # feature/ranking only, no spend
    python scripts/run_anomaly_pass.py --top-fraction 0.05 --cap 20
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

import polars as pl

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research.anomaly.grok_client import LabelFn, make_xai_label_fn  # noqa: E402
from research.anomaly.paths import ANOMALY_TABLE_PATH, GROK_SPEND_PATH, LABELS_JSONL_PATH  # noqa: E402
from research.anomaly.pipeline import load_table, run_pass, save_table  # noqa: E402
from research.anomaly.spend import GROK_BACKFILL_CAP_USD, SpendLedger  # noqa: E402
from research.data.folds import load_folds  # noqa: E402
from research.data.instruments import ROOTS  # noqa: E402
from research.data.loader import load_bars  # noqa: E402

log = logging.getLogger("run_anomaly_pass")


def _load_env_file(path: Path) -> None:
    """Minimal KEY=VALUE .env loader — matches scripts/build_research_corpus.py."""
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _no_op_label_fn() -> LabelFn:
    def _label(instrument, session_date, feature_summary):  # pragma: no cover - never called
        raise AssertionError("no-op label_fn was called despite top_fraction=0")

    return _label


def load_accessible_corpus() -> pl.DataFrame:
    """Concatenated (instrument, ts, ...) bars for all three roots, clipped
    to strictly before the sealed holdout — the same window
    scripts/build_research_corpus.py writes to corpus_path().
    """
    folds = load_folds()
    end = folds.holdout_start - timedelta(days=1)
    frames = [load_bars(root, folds.corpus_start, end, "unadjusted") for root in ROOTS]
    return pl.concat(frames)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--no-label", action="store_true", help="rebuild/rank only, no Grok calls")
    parser.add_argument("--top-fraction", type=float, default=0.10)
    parser.add_argument("--cap", type=float, default=GROK_BACKFILL_CAP_USD, help="Grok spend cap, USD")
    parser.add_argument("--table-path", default=str(ANOMALY_TABLE_PATH))
    parser.add_argument("--concurrency", type=int, default=4,
                        help="Grok label calls in flight at once (backs off on 429s)")
    args = parser.parse_args()

    _load_env_file(REPO_ROOT / ".env")
    table_path = Path(args.table_path)
    top_fraction = 0.0 if args.no_label else args.top_fraction

    if args.no_label:
        label_fn = _no_op_label_fn()
    else:
        api_key = os.environ.get("XAI_API_KEY")
        if not api_key:
            print("XAI_API_KEY not set (checked environment and .env) — pass --no-label to skip "
                  "regime labelling", file=sys.stderr)
            sys.exit(1)
        label_fn = make_xai_label_fn(api_key)

    spend = SpendLedger.load(GROK_SPEND_PATH)

    start = time.perf_counter()
    log.info("loading accessible corpus for %s", ", ".join(ROOTS))
    bars = load_accessible_corpus()
    existing_table = load_table(table_path)

    run = run_pass(
        bars,
        existing_table=existing_table,
        label_fn=label_fn,
        spend=spend,
        top_fraction=top_fraction,
        cap=args.cap,
        concurrency=args.concurrency,
        labels_path=LABELS_JSONL_PATH,
    )
    save_table(run.table, table_path)
    elapsed = time.perf_counter() - start

    log.info(
        "wrote %s: %d sessions, %d newly labelled, %d skipped, $%.2f Grok spend "
        "this session (%.1fs)",
        table_path,
        len(run.table),
        run.labelled,
        run.skipped,
        spend.total_usd,
        elapsed,
    )
    if run.budget_exhausted:
        log.error("Grok backfill budget cap ($%.2f) reached — labelling stopped early", args.cap)
    if run.interrupted:
        log.warning(
            "interrupted: %d label(s) from this run saved to %s and merged into %s "
            "(spend recorded: $%.2f) — re-run to label the rest",
            run.labelled, LABELS_JSONL_PATH, table_path, spend.total_usd,
        )
        logging.shutdown()
        if run.abandoned:
            os._exit(130)  # worker threads are still blocked on abandoned HTTP calls
        sys.exit(130)
    if run.aborted:
        log.error("labelling pass ABORTED early: %s", run.abort_reason)
        sys.exit(1)


if __name__ == "__main__":
    main()
