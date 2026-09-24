"""Annual hypothesis budget enforcement.

docs/research-loop/README.md: "30-50 hypotheses per year, enforced in the
ledger as a hard cap, not a budget alert." CLAUDE.md rule 6 is explicit that
when it's spent, the loop stops — that pause is intended behaviour, not a
bug to route around. This counts ledger ROWS (hypotheses tested), which is
distinct from `trial_count_at()` (research.ledger.api), which sums
`n_variants_swept` and feeds the Sharpe-cutoff and deflated-Sharpe
calculations. A single hypothesis can sweep many variants; the annual cap
limits how many distinct ideas the loop is allowed to log in a year.
"""
from __future__ import annotations

import sqlite3

ANNUAL_HYPOTHESIS_CAP = 50


class TrialBudgetExceeded(RuntimeError):
    """Raised when the annual hypothesis cap is already spent.

    Not a warning: CLAUDE.md rule 6 requires the loop to stop, and rule 12
    ("fail loud") requires that stop to be an exception, not a logged line
    the caller can ignore.
    """


def count_hypotheses_in_year(conn: sqlite3.Connection, year: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM hypotheses WHERE strftime('%Y', created_at) = ?",
        (str(year),),
    ).fetchone()
    return row[0]


def enforce_trial_budget(
    conn: sqlite3.Connection, year: int, cap: int = ANNUAL_HYPOTHESIS_CAP
) -> None:
    """Raise TrialBudgetExceeded if `year` has already logged `cap` hypotheses.

    Call this immediately before logging a new hypothesis for `year` — it
    tells you whether that write is allowed, not whether past writes were.
    """
    logged = count_hypotheses_in_year(conn, year)
    if logged >= cap:
        raise TrialBudgetExceeded(
            f"annual hypothesis cap ({cap}) already reached for {year}: "
            f"{logged} hypotheses logged. The loop must stop for the rest "
            f"of {year}, not continue past the cap."
        )
