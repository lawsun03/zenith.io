"""The Carver Sharpe-cutoff lookup table (docs/research-loop/gates.md, gate 5).

Source: Carver, *Systematic Trading*, table 4 — reproduced in
docs/research-loop/gates.md. If that table is ever revised, this constant
must be updated to match; it is not derived from the markdown file at
runtime (parsing prose tables at runtime is more fragile than a documented
manual sync, and this table changes on the order of "a published book gets
a new edition," not per-commit).

Gate 5 rejects a candidate whose observed OOS Sharpe is below the cutoff for
the trial count in force *at the time it was tested* — the ledger's
`sr_cutoff_applied` column exists precisely so that decision replays
correctly even if this table is later revised (docs/research-loop/README.md:
"reproducibility of the cutoff").
"""
from __future__ import annotations

import bisect
import math

# Rows: number of rules tested. Columns: years of data.
_TRIALS = (1, 5, 10, 50, 100)
_YEARS = (1, 5, 10, 30)

_TABLE: dict[int, tuple[float, ...]] = {
    1:   (1.5, 0.7, 0.5, 0.4),
    5:   (2.3, 1.1, 0.8, 0.5),
    10:  (2.8, 1.2, 0.8, 0.6),
    50:  (3.4, 1.5, 1.0, 0.6),
    100: (3.4, 1.5, 1.1, 0.7),
}

_MIN_TRIALS, _MAX_TRIALS = _TRIALS[0], _TRIALS[-1]
_MIN_YEARS, _MAX_YEARS = _YEARS[0], _YEARS[-1]


def _bracket(sorted_values: tuple[float, ...], x: float) -> tuple[float, float]:
    """Return the two adjacent table values bracketing x (equal if x is
    itself a table value, or at an edge)."""
    i = bisect.bisect_right(sorted_values, x) - 1
    i = max(0, min(i, len(sorted_values) - 2))
    return sorted_values[i], sorted_values[i + 1]


def _lerp(x0: float, x1: float, y0: float, y1: float, x: float) -> float:
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)


def _interp_years(row: tuple[float, ...], years: float) -> float:
    y0, y1 = _bracket(_YEARS, years)
    i0, i1 = _YEARS.index(y0), _YEARS.index(y1)
    return _lerp(y0, y1, row[i0], row[i1], years)


def sr_cutoff(n_trials: float, years: float) -> float:
    """Interpolated Sharpe-ratio cutoff for `n_trials` rules tested against
    `years` years of data.

    Interpolation scheme: linear across years (matching the README's own
    "interpolating between 10 and 30"); linear across log10(trials), since
    the table's trial counts are roughly log-spaced and the cutoff's
    sensitivity to trial count shrinks as trial count grows. Both schemes
    are monotonic wherever the underlying table is monotonic.

    Raises ValueError outside the table's validated range
    (1 <= n_trials <= 100, 1 <= years <= 30) rather than extrapolating a
    statistical table beyond where it's been checked (CLAUDE.md rule 12:
    "a gate that cannot be evaluated is a failure, not a pass").
    """
    if not (_MIN_TRIALS <= n_trials <= _MAX_TRIALS):
        raise ValueError(
            f"n_trials={n_trials} is outside the validated table range "
            f"[{_MIN_TRIALS}, {_MAX_TRIALS}]"
        )
    if not (_MIN_YEARS <= years <= _MAX_YEARS):
        raise ValueError(
            f"years={years} is outside the validated table range "
            f"[{_MIN_YEARS}, {_MAX_YEARS}]"
        )

    t0, t1 = _bracket(_TRIALS, n_trials)
    v_lo = _interp_years(_TABLE[int(t0)], years)
    v_hi = _interp_years(_TABLE[int(t1)], years)

    if t1 == t0:
        return v_lo

    log_t0, log_t1, log_t = math.log10(t0), math.log10(t1), math.log10(n_trials)
    return _lerp(log_t0, log_t1, v_lo, v_hi, log_t)
