"""Grok regime-labelling spend cap.

docs/research-loop/PHASE-PROMPTS.md phase 3: "cap the Grok backfill at
$150 of X Search spend. Log spend as you go and stop at the cap." Mirrors
research.stats.budget's pattern — raise *before* the write/call that would
exceed the cap, not a warning (CLAUDE.md rule 12: fail loud) — but persists
a running dollar total to a small JSON file rather than a ledger table,
since it must survive across separate backfill/nightly-job process
invocations and is never queried any other way (Rule 2: a flat running
total is simpler than a table for a number that's only ever
read-then-incremented).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

GROK_BACKFILL_CAP_USD = 150.0


class GrokBudgetExceeded(RuntimeError):
    """Raised when a label call would push cumulative spend past the cap.

    Not a warning: this is the loop's hard stop for Grok spend, the same
    way research.stats.budget.TrialBudgetExceeded is the hard stop for the
    annual hypothesis cap.
    """


@dataclass
class SpendLedger:
    path: Path
    total_usd: float = 0.0

    @classmethod
    def load(cls, path: Path) -> "SpendLedger":
        if path.exists():
            raw = json.loads(path.read_text())
            return cls(path=path, total_usd=float(raw.get("total_usd", 0.0)))
        return cls(path=path, total_usd=0.0)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"total_usd": self.total_usd}))

    def charge(self, amount_usd: float, *, cap: float = GROK_BACKFILL_CAP_USD, context: str = "") -> None:
        """Add `amount_usd` to the running total, persist it, and log it.

        Raises GrokBudgetExceeded *without* recording the charge if it
        would push the total past `cap` — the call that would have
        breached the cap never happened, so the ledger must not say it did.
        """
        if self.total_usd + amount_usd > cap:
            raise GrokBudgetExceeded(
                f"Grok backfill cap (${cap:.2f}) would be exceeded by this call "
                f"(+${amount_usd:.4f} on top of ${self.total_usd:.4f}) — {context}"
            )
        self.total_usd += amount_usd
        self._save()
        log.info(
            "grok spend: +$%.4f (%s) running total $%.4f / $%.2f cap",
            amount_usd,
            context,
            self.total_usd,
            cap,
        )
