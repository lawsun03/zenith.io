"""Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014).

The DSR is the probability that the observed Sharpe ratio is genuinely
positive after correcting for selection bias from testing multiple trials —
it answers "given that we tried N variants and this is the best/one we're
looking at, how much of the observed Sharpe is likely just multiple-testing
luck." This is what gate 6 (docs/research-loop/gates.md) uses, and CLAUDE.md
rule 7 is why `n_trials` must be the ledger's variant-weighted trial count,
not a row count.

Convention note (read before calling): `kurtosis` is the *non-excess*
(Pearson) convention, where a normal distribution has kurtosis 3.0 — this
matches the original paper. `scipy.stats.kurtosis` defaults to *excess*
kurtosis (normal = 0.0); if computing kurtosis with scipy, pass
`fisher=False`, or add 3.0 to the excess value before calling this function.
"""
from __future__ import annotations

import math

_EULER_MASCHERONI = 0.5772156649015329


def _std_normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _std_normal_ppf(p: float) -> float:
    """Inverse standard normal CDF (probit).

    Peter Acklam's rational approximation (public domain), refined with one
    step of Halley's method — no `scipy.stats.norm.ppf` dependency, accurate
    to about 1e-9 absolute error over (0, 1).
    """
    if not (0.0 < p < 1.0):
        raise ValueError(f"p must be in the open interval (0, 1), got {p}")

    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)

    p_low = 0.02425
    p_high = 1.0 - p_low

    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        x = (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
            (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
             ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)

    # One Halley step to tighten the rational approximation.
    e = _std_normal_cdf(x) - p
    u = e * math.sqrt(2.0 * math.pi) * math.exp(x * x / 2.0)
    x = x - u / (1.0 + x * u / 2.0)
    return x


def _sr_variance(observed_sr: float, skew: float, kurtosis: float, n_obs: int) -> float:
    """Var[SR_hat]: the sampling variance of ONE Sharpe estimate, assuming its
    estimator is asymptotically normal (Bailey & Lopez de Prado, eq. 2).
    Depends on the return distribution's own skew and (non-excess) kurtosis.

    This is the denominator of the PSR/DSR test statistic. It is NOT the
    cross-trial variance V[{SR_n}] that sets the expected-maximum benchmark —
    see deflated_sharpe_ratio.
    """
    variance = (1.0 - skew * observed_sr + ((kurtosis - 1.0) / 4.0) * observed_sr ** 2) / (n_obs - 1)
    if variance <= 0:
        raise ValueError(
            "computed Sharpe-ratio variance is not positive — check the skew/kurtosis "
            "inputs (kurtosis must be non-excess: 3.0 for a normal distribution, "
            "not 0.0)"
        )
    return variance


def _expected_max_sharpe(n_trials: int, sr_variance_across_trials: float) -> float:
    """SR0 = E[max SR_n] under the null of N independent zero-skill trials
    (Bailey & Lopez de Prado, eq. 10):

        SR0 = sqrt(V[{SR_n}]) * ((1 - g) * Phi^-1(1 - 1/N) + g * Phi^-1(1 - 1/(N e)))

    V[{SR_n}] is the variance of the Sharpe ratios ACROSS the trials — how
    spread out the whole search's results were — not the sampling variance
    of one estimate.
    """
    if n_trials <= 1:
        # A single trial involves no selection: the benchmark is zero.
        return 0.0
    z1 = _std_normal_ppf(1.0 - 1.0 / n_trials)
    z2 = _std_normal_ppf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(sr_variance_across_trials) * (
        (1.0 - _EULER_MASCHERONI) * z1 + _EULER_MASCHERONI * z2
    )


def empirical_sr_variance(sharpes: "list[float] | tuple[float, ...]") -> float:
    """Sample variance (ddof=1) of the per-period Sharpe ratios of the trials
    actually run — the paper's V[{SR_n}]. Use the SAME per-period units as
    the observed_sr passed to deflated_sharpe_ratio.

    Raises ValueError with fewer than two trials or zero spread: with no
    measurable dispersion there is nothing to estimate, and silently
    returning 0 would make SR0 = 0 and remove the multiple-testing penalty.
    """
    n = len(sharpes)
    if n < 2:
        raise ValueError(f"need at least 2 trial Sharpe ratios, got {n}")
    mean = sum(sharpes) / n
    var = sum((x - mean) ** 2 for x in sharpes) / (n - 1)
    if var <= 0:
        raise ValueError("trial Sharpe ratios have zero variance")
    return var


def null_sr_variance(n_obs: int) -> float:
    """1 / (n_obs - 1): the spread of Sharpe estimates across trials when
    every trial has zero true skill and returns are roughly normal (the
    sampling variance of an SR estimate at SR = 0).

    A principled stand-in for V[{SR_n}] when too few trials have been
    recorded to measure the real spread. It is the null hypothesis's own
    value, so it is not a free parameter — but a real search usually shows
    MORE spread than this, which makes the empirical value the stricter and
    preferred input once it exists.
    """
    if n_obs < 2:
        raise ValueError(f"n_obs must be >= 2, got {n_obs}")
    return 1.0 / (n_obs - 1)


def deflated_sharpe_ratio(
    observed_sr: float,
    n_trials: int,
    skew: float,
    kurtosis: float,
    n_obs: int,
    *,
    sr_variance_across_trials: float | None = None,
) -> float:
    """Probability (in [0, 1]) that the true Sharpe ratio is positive, after
    deflating `observed_sr` for having been selected out of `n_trials`:

        DSR = Phi( (SR - SR0) / sqrt(Var[SR_hat]) )
            = Phi( (SR - SR0) * sqrt(T - 1) / sqrt(1 - skew*SR + (kurt-1)/4 * SR^2) )

    Reproduces the paper's worked example (annualized SR 2.5, N = 100, daily
    T = 1250, skew -3, kurtosis 10, annualized V[{SR_n}] = 1/2) -> 0.9004
    (tests/test_statistics.py).

    Args:
        observed_sr: the candidate's observed per-period Sharpe ratio.
        n_trials: number of independent trials searched over — the ledger's
            variant-weighted trial count (CLAUDE.md rule 7), not a row count.
        skew: skewness of the per-period returns.
        kurtosis: non-excess kurtosis of the per-period returns (normal = 3.0
            — see module docstring).
        n_obs: number of return observations the Sharpe ratio was estimated
            from.
        sr_variance_across_trials: V[{SR_n}], the variance of the per-period
            Sharpe ratios across the trials (empirical_sr_variance, or
            null_sr_variance when too few trials exist to measure it).
            Required when n_trials > 1; ignored when n_trials == 1.

    Raises:
        ValueError: if n_obs < 2; n_trials < 1; the SR sampling variance is
            not positive; or n_trials > 1 without a positive
            sr_variance_across_trials. There is deliberately no fallback to
            the sampling variance — that substitution reports ~0.98 where
            the paper's answer is 0.90 (CLAUDE.md rule 12: fail loud).
    """
    if n_obs < 2:
        raise ValueError(f"n_obs must be >= 2, got {n_obs}")
    if n_trials < 1:
        raise ValueError(f"n_trials must be >= 1, got {n_trials}")

    sampling_variance = _sr_variance(observed_sr, skew, kurtosis, n_obs)

    if n_trials > 1:
        if sr_variance_across_trials is None:
            raise ValueError(
                "sr_variance_across_trials is required when n_trials > 1 — pass "
                "empirical_sr_variance(...) or null_sr_variance(n_obs)"
            )
        if sr_variance_across_trials <= 0:
            raise ValueError(
                f"sr_variance_across_trials must be > 0, got {sr_variance_across_trials}"
            )
        sr_benchmark = _expected_max_sharpe(n_trials, sr_variance_across_trials)
    else:
        sr_benchmark = 0.0

    z = (observed_sr - sr_benchmark) / math.sqrt(sampling_variance)
    return _std_normal_cdf(z)
