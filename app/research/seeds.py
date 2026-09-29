"""The two metals candidates from the SI/GC study (spec §3, §12).

Both were picked while 2023–26 was being read, so their holdout is spent:
they're registered with holdout_contaminated, the vault refuses them, and
their only clean test is the forward test. Neither is implemented in the
engine yet (step 8 re-implements and reproduces them), so this registers
the families only; variants arrive with the code.
"""
from __future__ import annotations

import logging

from app.research.ledger import Ledger

log = logging.getLogger(__name__)

_SOURCE = "SI/GC manual study, Sep 2026 (docs/research-protocol-spec.md §0, §12)"

SEED_FAMILIES = (
    {"slug": "gold-pdhl-sweep",
     "name": "Gold prior-day high/low sweep (MGC)",
     "hypothesis": (
         "08:25-12:00 ET, gold trades beyond the prior full-session high or low; within 30 "
         "one-minute bars a 1-minute close lands back inside the level, at least 0.05xATR14 "
         "from the sweep extreme. Enter at market on that close. Stop 1 tick beyond the "
         "extreme, target 1.5R, exit 13:25, first signal per day. Study net R/trade: "
         "2015-22 -0.03, 2023-25 -0.06, 2026 +0.26.")},
    {"slug": "silver-825-orb",
     "name": "Silver 8:25 five-minute range breakout (SIL)",
     "hypothesis": (
         "Range = 08:25-08:29 ET high/low. Stop orders 1 tick beyond each side, active "
         "08:30-10:00, first fill wins. Stop 1 tick beyond the opposite side, target 1.5R, "
         "exit 13:25; skip the day if one bar breaks both sides. Study net R/trade: "
         "2015-22 -0.23, 2023-25 0.00, 2026 +0.17.")},
)


def seed_family_id(slug: str, protocol_id: str) -> str:
    return f"{slug}@{protocol_id}"


def register_seeds(ledger: Ledger, protocol_id: str, created_by: str) -> list[str]:
    """Idempotent. Returns the ids newly registered."""
    added = []
    for f in SEED_FAMILIES:
        fid = seed_family_id(f["slug"], protocol_id)
        cur = ledger.execute(
            "INSERT OR IGNORE INTO families (id, name, source_type, source_ref, hypothesis, "
            "created_by, protocol_id, holdout_contaminated) VALUES (?, ?, 'user', ?, ?, ?, ?, 1)",
            (fid, f["name"], _SOURCE, f["hypothesis"], created_by, protocol_id))
        if cur.rowcount:
            added.append(fid)
            log.info("seed family %s registered under %s (holdout_contaminated)", fid, protocol_id)
    return added
