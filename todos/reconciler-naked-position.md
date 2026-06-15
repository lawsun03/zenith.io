title: Reconciler naked position detection
priority: medium
status: done
category: risk
created: 2026-06-08
---
DONE 2026-06-09: shipped as the exit-coverage monitor on branch feat/exit-coverage-monitor.
The reconciler now verifies every open contract has a working stop+target on the exchange
each tick (when contract counts match) and escalates grace -> re-attach missing leg ->
flatten if the stop can't be restored. Plan: docs/superpowers/plans/2026-06-08-exit-coverage-monitor.md
Design: docs/superpowers/specs/2026-06-08-exit-coverage-monitor-design.md

SUPERSEDED 2026-06-08: persistence approach rejected. Reframed as an exit-coverage
monitor (every open contract must have a working stop+target on the exchange; escalating
grace -> re-attach missing leg -> flatten if stop unrecoverable). See design:
docs/superpowers/specs/2026-06-08-exit-coverage-monitor-design.md

---
After a bot restart mid-position, _exit_groups is cleared. Open positions on the exchange have no bracket tracking. The reconciler sees matching contract counts (no drift) and takes no action — leaving a naked position indefinitely.

Add a has_exit_tracking() method to the broker protocol. Reconciler checks: if open_contracts != 0 and no exit tracking, emit "naked_position" drift kind and alert operator. Grace period needed to avoid false positives during bracket placement race.

---
Findings 2026-06-08 (investigation, not yet implemented):

NOT implemented. Confirmed absent:
- No has_exit_tracking() on the broker protocol (app/broker/protocol.py).
- Reconciler drift_kind is only "contract_count" | "balance" | None (reconciler.py:85). No "naked_position".
- No bracket-placement grace period for naked detection.
- No bracket re-attach logic anywhere — nothing re-creates stop/target orders for an
  untracked open position. The only handling is flatten + lockout (below).

IMPORTANT — the premise above is stale. The catastrophic case ("naked position left
indefinitely") is NOT reachable in current code:
- RiskState.open_contracts defaults to 0 and is never persisted (state.py:68 — no save/load).
- So after a mid-position restart, internal=0 but broker shows the position. The counts
  DIVERGE, they don't match.
- Tick 1: first-tick grace, no action. Tick 2: broker != internal -> contract_count drift
  -> _emergency_flatten() + RECONCILE_DRIFT session lockout (reconciler.py:354-396, 437-504).
- Net: a post-restart naked position is flattened + locked out within ~2 ticks (<=60s).
  Safe, never naked indefinitely — but blunt (kills a possibly-good position, halts the session).

Reframed scope (the real value-add): SMARTER naked-position handling — re-attach brackets
(or targeted single-instrument flatten) instead of the full flatten + session lockout.
This is an ergonomics refinement, not a money-protection gap. Downgraded medium -> low.

Caveat: if open_contracts persistence across restart is ever added, the counts WOULD match,
the original failure mode becomes reachable, and this jumps back to medium/high. Revisit then.

Per CLAUDE.md Rule 2: don't add a third reconciler path unless the existing two
(contract_count, balance) are genuinely insufficient — and today they cover the dangerous case.
