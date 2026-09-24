"""Statistics kit tests — deflated Sharpe, CSCV/PBO, Carver cutoff table.

Every expected value here was computed OUTSIDE this codebase, from the
published formulas, without reading the bodies of research/stats/*.py
(CLAUDE.md rule 11: a generated test agrees with the implementation's bugs).

Sources:
  [DSR]  Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio:
         Correcting for Selection Bias, Backtest Overfitting and
         Non-Normality", J. Portfolio Management 40(5).
         https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf
  [PBO]  Bailey, Borwein, Lopez de Prado & Zhu (2015), "The Probability of
         Backtest Overfitting", J. Computational Finance.
         https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
  [CARVER] Carver, *Systematic Trading* (2015), ch. 3, table 4.

Reference computation: scipy.stats.norm for the normal CDF/PPF, and an
independent CSCV written from [PBO] section 2 (train set = concatenation of
S/2 blocks, metric on the concatenated rows, omega = rank/(N+1) with rank 1
= worst out-of-sample, lambda = ln(omega/(1-omega)), PBO = P(lambda <= 0)).

Formulas as used for the reference values ([DSR] eqs. and PSR from Bailey &
Lopez de Prado 2012):

    PSR(SR*) = Phi( (SR - SR*) * sqrt(T - 1) / sqrt(1 - skew*SR + (kurt - 1)/4 * SR^2) )
    SR0      = sqrt(V[{SR_n}]) * ( (1 - g) * Phi^-1(1 - 1/N) + g * Phi^-1(1 - 1/(N e)) )
    DSR      = PSR(SR0)

with SR per-period (not annualized), kurt non-excess (normal = 3), g the
Euler-Mascheroni constant, and V[{SR_n}] the variance of the Sharpe ratios
ACROSS the N trials. With N = 1 there is no selection, so SR0 = 0 and
DSR = PSR(0).

API NOTE — these tests require one change to research.stats.deflated_sharpe:
the paper's SR0 uses the cross-trial variance V[{SR_n}], which the current
signature has no way to receive. The tests pass it as the keyword argument
`sr_variance_across_trials` (per-period units, same as observed_sr). In gate
6 that value is the variance of `sharpe_oos` across the ledger's trials.
It is required whenever n_trials > 1 — see test_deflated_sharpe_rejects_invalid_inputs.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, strategies as st

from research.stats.cscv import probability_of_backtest_overfitting as pbo
from research.stats.cutoff_table import sr_cutoff
from research.stats.deflated_sharpe import deflated_sharpe_ratio as dsr

# ---------------------------------------------------------------------
# [DSR] worked example — "A NUMERICAL EXAMPLE" section of the paper.
# Annualized SR 2.5 on 5 years of daily data, 100 trials, skew -3,
# kurtosis 10, annualized cross-trial SR variance 1/2. The paper reports
# DSR ~= 0.9004. Independent recomputation: 0.90039683.
# ---------------------------------------------------------------------
PAPER_SR = 2.5 / math.sqrt(250)      # 0.158114 per day
PAPER_V_ACROSS = 0.5 / 250           # 0.002 per day^2
PAPER_T = 1250
PAPER_SKEW = -3.0
PAPER_KURT = 10.0                    # non-excess
PAPER_N = 100

# ---------------------------------------------------------------------
# Item 5 — Carver cutoff table: monotonicity (unchanged)
# ---------------------------------------------------------------------


@given(
    n_trials=st.floats(min_value=1, max_value=100),
    years=st.floats(min_value=1, max_value=30),
    d_trials=st.floats(min_value=0, max_value=99),
    d_years=st.floats(min_value=0, max_value=29),
)
def test_cutoff_table_monotonic_in_both_dimensions(n_trials, years, d_trials, d_years):
    """More trials tested => the cutoff never decreases; more years of data
    => the cutoff never increases. This is what makes the table usable as a
    gate at all: a candidate can't dodge a stricter cutoff by understating
    the trial count, and more history should only ever make the bar easier
    to clear, never harder."""
    n_trials_hi = min(n_trials + d_trials, 100.0)
    years_hi = min(years + d_years, 30.0)

    assert sr_cutoff(n_trials_hi, years) >= sr_cutoff(n_trials, years) - 1e-9
    assert sr_cutoff(n_trials, years_hi) <= sr_cutoff(n_trials, years) + 1e-9


# ---------------------------------------------------------------------
# Item 3 — Deflated Sharpe ratio
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "observed_sr, skew, kurtosis, n_obs, expected_psr",
    [
        # (SR, skew, kurt, T) -> Phi(SR*sqrt(T-1)/sqrt(1 - skew*SR + (kurt-1)/4*SR^2))
        (0.10, -0.5, 4.0, 500, 0.9850816470),
        (0.00, 0.0, 3.0, 100, 0.5000000000),   # zero SR is a coin flip, exactly
        (0.01, 0.0, 3.0, 100, 0.5396278699),
        (-0.05, 0.0, 3.0, 100, 0.3095299774),  # negative SR: below one half, not zero
        (0.05, 0.3, 5.0, 250, 0.7863919728),
    ],
)
def test_deflated_sharpe_matches_psr_when_n_trials_is_one(
    observed_sr, skew, kurtosis, n_obs, expected_psr
):
    """With one trial there is no selection to correct for: SR0 = 0 and the
    DSR is the plain probabilistic Sharpe ratio benchmarked against zero.

    These values catch a scaling error directly. The statistic grows with
    sqrt(T - 1); an implementation that grows it with T - 1 (dividing by the
    SR variance instead of its standard deviation) turns 0.5396 into ~0.84
    and 0.3095 into ~0.0.
    """
    got = dsr(
        observed_sr=observed_sr, n_trials=1, skew=skew, kurtosis=kurtosis, n_obs=n_obs
    )
    assert got == pytest.approx(expected_psr, abs=1e-6)


def test_deflated_sharpe_decreases_as_trials_increase():
    """More trials => a higher expected maximum Sharpe from pure luck (SR0)
    => a lower probability the observed SR is genuine.

    SR0 is strictly increasing in N for every N >= 1 (SR0 = 0 at N = 1 and
    positive from N = 2), so the DSR must fall strictly at every step — there
    is no plateau inside this range. Values: paper inputs, independent
    recomputation.
    """
    expected = {
        1: 0.99999686,     # PSR(0): no selection penalty
        2: 0.99994159,
        5: 0.99861941,
        10: 0.99387964,
        50: 0.94615774,
        100: 0.90039683,   # the paper's example
        1000: 0.63991496,
    }
    got = {
        n: dsr(
            observed_sr=PAPER_SR,
            n_trials=n,
            skew=PAPER_SKEW,
            kurtosis=PAPER_KURT,
            n_obs=PAPER_T,
            sr_variance_across_trials=PAPER_V_ACROSS,
        )
        for n in expected
    }
    for n, want in expected.items():
        assert got[n] == pytest.approx(want, abs=1e-6), f"n_trials={n}"

    ordered = [got[n] for n in sorted(got)]
    assert all(a > b for a, b in zip(ordered, ordered[1:])), ordered


def test_deflated_sharpe_matches_bailey_lopez_de_prado_worked_example():
    """[DSR], "A NUMERICAL EXAMPLE": a strategist finds an annualized Sharpe
    of 2.5 after 100 trials on five years of daily data. The paper's answer
    is DSR ~= 0.9004 — only a 90% chance the edge is real, so NOT significant
    at 95% despite the impressive headline.

    Intermediate values from the independent recomputation:
        per-day SR              0.158114
        SR0 (expected max)      0.113172
        DSR                     0.900397

    Tolerance 5e-4 covers the paper's four-decimal rounding. If the
    implementation derives SR0 from the SR estimator's own variance instead
    of the cross-trial variance it returns ~0.9765 here — a materially
    overconfident answer on exactly the question gate 6 exists to ask.
    """
    got = dsr(
        observed_sr=PAPER_SR,
        n_trials=PAPER_N,
        skew=PAPER_SKEW,
        kurtosis=PAPER_KURT,
        n_obs=PAPER_T,
        sr_variance_across_trials=PAPER_V_ACROSS,
    )
    assert got == pytest.approx(0.9004, abs=5e-4)
    assert got < 0.95, "the paper's point: this strategy fails at 95% confidence"


def test_deflated_sharpe_rejects_invalid_inputs():
    """Boundary decisions:

    - n_obs = 2 is the smallest valid sample (sqrt(T - 1) = 1); n_obs = 1
      gives sqrt(0) and must raise.
    - n_trials = 1 is valid; 0 and negatives must raise.
    - The SR-variance term 1 - skew*SR + (kurt-1)/4*SR^2 must be strictly
      positive. Zero is rejected as well as negative: it means dividing by
      zero, and a variance of exactly zero is a data problem, not an
      infinitely confident result.
    - With n_trials > 1, sr_variance_across_trials is required and must be
      > 0. There is deliberately no fallback to the estimator variance —
      a quiet fallback would report ~0.98 where the paper says 0.90
      (CLAUDE.md rule 12: fail loud).
    """
    ok = dict(observed_sr=0.1, skew=0.0, kurtosis=3.0)

    # sample length
    dsr(n_trials=1, n_obs=2, **ok)  # smallest valid — must not raise
    with pytest.raises(ValueError):
        dsr(n_trials=1, n_obs=1, **ok)
    with pytest.raises(ValueError):
        dsr(n_trials=1, n_obs=0, **ok)

    # trial count
    with pytest.raises(ValueError):
        dsr(n_trials=0, n_obs=100, **ok)
    with pytest.raises(ValueError):
        dsr(n_trials=-5, n_obs=100, **ok)

    # SR variance term: 1 - 2*1 + (5-1)/4*1 = 0  -> zero, reject
    with pytest.raises(ValueError):
        dsr(observed_sr=1.0, n_trials=1, skew=2.0, kurtosis=5.0, n_obs=100)
    # 1 - 3*1 + (1-1)/4*1 = -2 -> negative, reject
    with pytest.raises(ValueError):
        dsr(observed_sr=1.0, n_trials=1, skew=3.0, kurtosis=1.0, n_obs=100)

    # cross-trial variance: required and positive when n_trials > 1
    with pytest.raises(ValueError):
        dsr(n_trials=10, n_obs=100, **ok)
    with pytest.raises(ValueError):
        dsr(n_trials=10, n_obs=100, sr_variance_across_trials=0.0, **ok)
    with pytest.raises(ValueError):
        dsr(n_trials=10, n_obs=100, sr_variance_across_trials=-0.01, **ok)


# ---------------------------------------------------------------------
# Item 4 — CSCV / probability of backtest overfitting
# ---------------------------------------------------------------------

# Fixed literal matrix for the worked example: T=16 periods, N=4 variants,
# S=4 blocks of 4 rows -> C(4,2) = 6 splits. No RNG, so the expected value
# cannot drift with numpy versions.
_WORKED = np.array(
    [
        [0.012, -0.004, 0.006, 0.001],
        [0.008, 0.003, -0.002, 0.004],
        [-0.003, 0.009, 0.004, -0.006],
        [0.005, -0.007, 0.011, 0.002],
        [-0.010, 0.006, -0.001, 0.007],
        [0.002, -0.003, 0.008, -0.002],
        [0.007, 0.010, -0.005, 0.003],
        [-0.004, 0.001, 0.002, 0.009],
        [0.003, -0.008, 0.005, -0.001],
        [-0.006, 0.004, -0.009, 0.006],
        [0.009, -0.002, 0.003, -0.004],
        [0.001, 0.007, 0.006, 0.005],
        [-0.002, 0.005, -0.004, 0.008],
        [0.006, -0.006, 0.010, -0.003],
        [-0.008, 0.002, 0.001, 0.004],
        [0.004, 0.008, -0.003, -0.005],
    ]
)


def test_pbo_is_high_for_pure_noise_variants():
    """i.i.d. N(0,1) noise in every column: nothing real for in-sample
    selection to find, so the IS-best variant's out-of-sample rank is
    uniform on 1..N. With N = 20 (even), lambda <= 0 exactly when
    rank <= 10, so the expected PBO is 10/20 = 0.5 — exactly.

    A single matrix is too noisy to test against 0.5: across seeds the
    independent reference ranged 0.27–0.85, because the 252 splits share
    data and are strongly correlated. Averaging 20 independent matrices
    gives a standard error of ~0.034, so [0.40, 0.60] is a ~3-sigma band.
    """
    rng = np.random.default_rng(20260923)
    vals = [pbo(rng.standard_normal((1000, 20)), n_splits=10) for _ in range(20)]
    assert 0.40 <= float(np.mean(vals)) <= 0.60


def test_pbo_is_low_when_one_variant_has_a_persistent_edge():
    """Column 0 gets +0.25 per period on N(0,1) noise (a per-period Sharpe
    near 0.25); the other 19 columns are pure noise. Over 500-row training
    sets that edge beats the luckiest noise column almost every time, and
    stays top-ranked out of sample, so lambda > 0 in essentially every
    split. The independent reference gave PBO = 0.0 on 59 of 60 seeds, with
    a maximum of 0.008 — the threshold of 0.05 leaves wide margin.
    """
    rng = np.random.default_rng(7)
    m = rng.standard_normal((1000, 20))
    m[:, 0] += 0.25
    assert pbo(m, n_splits=10) < 0.05


def test_pbo_hand_derivable_extremes():
    """Two cases small enough to verify on paper (N=2, S=2, two splits).

    Anti-persistent: variant 0 is good in block 0 and bad in block 1;
    variant 1 is the mirror image. Whichever block trains, the IS winner is
    the OOS loser (rank 1 of 2 -> omega = 1/3 -> lambda < 0) in BOTH splits,
    so PBO = 1.0.

    Persistent: variant 0 beats variant 1 in both blocks, so the IS winner is
    always the OOS winner (omega = 2/3 -> lambda > 0), so PBO = 0.0.
    """
    anti = np.array([[0.02, -0.01], [0.01, -0.02], [-0.01, 0.02], [-0.02, 0.01]])
    persistent = np.array([[0.02, -0.01], [0.01, -0.02], [0.03, -0.02], [0.01, -0.01]])
    assert pbo(anti, n_splits=2) == pytest.approx(1.0)
    assert pbo(persistent, n_splits=2) == pytest.approx(0.0)


def test_pbo_matches_worked_example_from_bailey_et_al():
    """[PBO]'s examples are Monte-Carlo experiments, not a reproducible
    matrix, so this uses a fixed literal matrix (_WORKED) and an
    independently computed reference. Split-by-split (rank 1 = worst of 4):

        train (0,1) test (2,3): IS-best col2, OOS rank 2 -> overfit
        train (0,2) test (1,3): IS-best col0, OOS rank 1 -> overfit
        train (0,3) test (1,2): IS-best col2, OOS rank 2 -> overfit
        train (1,2) test (0,3): IS-best col3, OOS rank 1 -> overfit
        train (1,3) test (0,2): IS-best col1, OOS rank 1 -> overfit
        train (2,3) test (0,1): IS-best col3, OOS rank 3 -> not overfit

    PBO = 5/6. Per [PBO] section 2, the IS and OOS metric is computed on the
    CONCATENATED rows of the chosen blocks, not averaged across blocks.
    """
    assert pbo(_WORKED, n_splits=4) == pytest.approx(5 / 6, abs=1e-12)


def test_pbo_rejects_invalid_shapes():
    """ValueError for anything CSCV cannot run on honestly."""
    two_col = np.zeros((16, 2)) + np.arange(16)[:, None] * 1e-3
    with pytest.raises(ValueError):
        pbo(np.zeros((16, 1)), n_splits=4)          # one variant: nothing to select between
    with pytest.raises(ValueError):
        pbo(np.zeros((16,)), n_splits=4)            # 1-D, not (T, N)
    with pytest.raises(ValueError):
        pbo(two_col, n_splits=3)                    # odd S: no equal train/test halves
    with pytest.raises(ValueError):
        pbo(two_col, n_splits=6)                    # 6 does not divide 16
    with pytest.raises(ValueError):
        pbo(two_col, n_splits=0)


# ---------------------------------------------------------------------
# Item 5 — Carver cutoff table: exact published values
# ---------------------------------------------------------------------

# [CARVER] table 4, verbatim: Sharpe cutoff for a 5% chance of accepting a
# truly unprofitable rule. Rows = rules tested, columns = years of data.
_CARVER = {
    1: {1: 1.5, 5: 0.7, 10: 0.5, 30: 0.4},
    5: {1: 2.3, 5: 1.1, 10: 0.8, 30: 0.5},
    10: {1: 2.8, 5: 1.2, 10: 0.8, 30: 0.6},
    50: {1: 3.4, 5: 1.5, 10: 1.0, 30: 0.6},
    100: {1: 3.4, 5: 1.5, 10: 1.1, 30: 0.7},
}


@pytest.mark.parametrize(
    "n_trials, years, published",
    [(n, y, v) for n, row in _CARVER.items() for y, v in row.items()],
)
def test_cutoff_table_matches_published_values_at_grid_points(n_trials, years, published):
    """All 20 entries of Carver's table 4. Interpolation must add zero error
    on the grid."""
    assert sr_cutoff(n_trials, years) == pytest.approx(published, abs=1e-12)


@pytest.mark.parametrize(
    "n_trials, expected",
    [
        # v10 + (16 - 10) / (30 - 10) * (v30 - v10)
        (1, 0.47),    # 0.5 + 0.3 * (0.4 - 0.5)
        (5, 0.71),    # 0.8 + 0.3 * (0.5 - 0.8)
        (10, 0.74),   # 0.8 + 0.3 * (0.6 - 0.8)
        (50, 0.88),   # 1.0 + 0.3 * (0.6 - 1.0)
        (100, 0.98),  # 1.1 + 0.3 * (0.7 - 1.1)
    ],
)
def test_cutoff_table_sixteen_year_interpolation(n_trials, expected):
    """16 years is this project's corpus length (2010-06 onward). The
    documented scheme (research.stats.cutoff_table) is linear in years, so
    16 years sits 6/20 = 0.3 of the way from the 10-year column to the
    30-year column. Confirmed against the module docstring before asserting.
    """
    assert sr_cutoff(n_trials, 16) == pytest.approx(expected, abs=1e-9)


def test_cutoff_table_log_trials_interpolation_off_grid():
    """Between trial-count rows the documented scheme is linear in
    log10(trials). 30 trials at 10 years sits between the 10-trial (0.8) and
    50-trial (1.0) rows at fraction (log10 30 - 1) / (log10 50 - 1)
    = 0.682606, giving 0.8 + 0.682606 * 0.2 = 0.936521.
    """
    assert sr_cutoff(30, 10) == pytest.approx(0.936521, abs=1e-6)
