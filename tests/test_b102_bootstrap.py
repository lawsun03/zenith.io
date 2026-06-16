"""B102: block-bootstrap CIs for funded-pipeline headline metrics.

Why: funded_sim replays ONE deterministic P&L path; decisions were made on
13-vs-14-bust deltas with no uncertainty quantification. These tests verify
the bootstrap machinery behaves correctly before trusting CI results for
candidate comparisons.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.backtest.funded_sim import bootstrap_pipeline

D = Decimal
T0 = datetime(2023, 1, 3, 18, 0, tzinfo=timezone.utc)  # 2023-01-03 12:00 CT


def _daily(pnls: list[str]) -> list[tuple[datetime, Decimal]]:
    out, ts = [], T0
    for p in pnls:
        out.append((ts, D(p)))
        ts += timedelta(days=1)
    return out


def test_ci_ordering():
    """p5 <= p25 <= p50 <= p75 <= p95 for every metric — the invariant that
    makes CIs meaningful. A bootstrap that violates this has a sorting bug."""
    pnls = (["200", "-100", "300", "-50", "100", "0", "-200", "150"] * 25)[:200]
    result = bootstrap_pipeline(_daily(pnls), n_resamples=100, block_len=10, seed=7)
    for metric in ("xfa_net", "xfa_busts", "combine_passes"):
        ci = result[metric]
        assert ci[5] <= ci[25] <= ci[50] <= ci[75] <= ci[95], (
            f"{metric} CI ordering violated: {ci}"
        )


def test_reproducibility():
    """Same seed => identical result every time. Seeded bootstrap is the only
    way two researchers comparing configs get the same CI tables."""
    pnls = ["300", "-150", "200", "-100", "50"] * 40
    daily = _daily(pnls)
    r1 = bootstrap_pipeline(daily, n_resamples=50, block_len=10, seed=42)
    r2 = bootstrap_pipeline(daily, n_resamples=50, block_len=10, seed=42)
    assert r1 == r2


def test_different_seeds_differ():
    """Different seeds should produce different (not necessarily equal) results —
    verifies the RNG is actually being used, not bypassed."""
    pnls = ["200", "-100", "300"] * 40
    daily = _daily(pnls)
    r1 = bootstrap_pipeline(daily, n_resamples=50, block_len=10, seed=1)
    r2 = bootstrap_pipeline(daily, n_resamples=50, block_len=10, seed=99)
    # At least one metric should differ across seeds
    assert r1["xfa_net"] != r2["xfa_net"] or r1["xfa_busts"] != r2["xfa_busts"]


def test_constant_positive_series_tight_ci():
    """A constant-win series has near-zero sampling variance — CIs should be
    tight (p5 and p95 very close). If CIs are wide here, the resampling is
    introducing artificial variance."""
    # +$600/day for 200 days: always passes Combine, never busts XFA
    pnls = ["600"] * 200
    result = bootstrap_pipeline(_daily(pnls), n_resamples=200, block_len=20, seed=0)
    net_ci = result["xfa_net"]
    # p95 - p5 should be < 5% of p50 for a near-deterministic series
    spread = net_ci[95] - net_ci[5]
    assert spread < abs(net_ci[50]) * 0.05, (
        f"CIs unexpectedly wide for constant series: {net_ci}"
    )


def test_series_too_short_raises():
    """Series shorter than block_len should raise ValueError, not silently
    produce garbage CIs."""
    daily = _daily(["100"] * 5)
    with pytest.raises(ValueError, match="too short"):
        bootstrap_pipeline(daily, block_len=20)


def test_result_has_expected_keys():
    """The result dict must carry n_resamples, block_len, series_len for
    provenance — future JOURNAL entries quote these to document their CI method."""
    pnls = ["100", "-50"] * 60
    r = bootstrap_pipeline(_daily(pnls), n_resamples=10, block_len=10)
    assert r["n_resamples"] == 10
    assert r["block_len"] == 10
    assert r["series_len"] == 120
    for metric in ("xfa_net", "xfa_busts", "combine_passes"):
        assert set(r[metric].keys()) == {5, 25, 50, 75, 95}
