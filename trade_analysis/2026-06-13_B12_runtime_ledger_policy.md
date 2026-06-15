# B12 — Runtime Ledger Policy: Git-Wipe Hazard

**Date:** 2026-06-13  
**Session:** wk1-b12  
**Status:** Backfill complete. Policy decision deferred to Lawrence.

---

## What happened

On 2026-06-10 23:35 PT a `git reset --hard HEAD` (or equivalent restore) wiped
`trades/trades.csv` back to its committed state. The writer (journaling.py) was
healthy and continued running — it just wrote into the reset file from that point
onward. The restore erased 22 rows spanning 2026-06-10T01:32Z → 2026-06-12T14:59Z.

Daily snapshot files (`trades_2026-06-10.csv`) are **untracked** and **survived**
because git does not touch untracked files on `reset --hard`.

---

## Backfill completed (2026-06-13)

`trades_2026-06-10.csv` contained 20 recoverable rows
(2026-06-10T23:49Z → 2026-06-11T04:46Z). These have been inserted into
`trades.csv` before the 2026-06-12T14:59Z entry (no duplicates detected via
`broker_order_id` check).

**Known remaining gaps:**

| Period | Status |
|---|---|
| 2026-06-10T01:32Z → 2026-06-10T23:49Z | Unrecoverable — no daily file for this window |
| 2026-06-11T04:21Z MNQ long ENTRY (order 3112285061) | Exit row missing — either flushed to a non-existent 2026-06-11 daily file or never written |
| 2026-06-11T04:47Z → 2026-06-12T14:59Z | Unrecoverable — no daily file for this window |

The MNQ ENTRY at 04:21Z 06-11 is an orphaned row (no exit). It should not affect
aggregate PnL calculations as long as analysis scripts filter for ENTRY/EXIT pairs.
The realized_pnl column is only non-zero on EXIT rows.

---

## Policy options for Lawrence

The root cause: `trades/trades.csv` is git-tracked. Any `git restore`, `reset --hard`,
or `checkout` on the tree can overwrite it. This will happen again.

### Option A — Untrack the rolling files (recommended immediate fix)

Add to `.gitignore`:
```
trades/trades.csv
trades/excursions.csv
trades/rejections.csv
```

Keep the daily snapshot files tracked (they already are via `.gitignore` exemptions
or simply by being untracked — either works). Cloud analysis reads the daily files
or the rolling file directly from the live machine.

**Pros:**
- Zero code changes
- Git can never destroy the rolling file again
- Daily snapshots already survive — no behavior change for backups
- Immediate fix: apply today

**Cons:**
- `git status` no longer shows trades.csv changes (not a real con — it's a runtime file)
- A bad disk event or accidental `rm` of the rolling file is not recoverable from git
  (but was never meaningfully recoverable anyway since the committed version was stale)

**Implementation:** edit `.gitignore`, `git rm --cached trades/trades.csv trades/excursions.csv trades/rejections.csv`

---

### Option B — Commit-on-write

After each row append to trades.csv, run `git add trades/trades.csv && git commit -m "..."`.

**Pros:**
- Rolling file is always in git history

**Cons:**
- Every single trade triggers a git commit (noisy, slow)
- Complicates the writer: requires git binary in the write path
- Still susceptible to a fast `reset --hard` that happens before the commit fires
- Not worth implementing

**Verdict: reject.**

---

### Option C — Move cloud-sync to the Outbox channel

The `app/sync/` Outbox already handles durable delivery. Stop writing to `trades.csv`
as a git-tracked file; instead, have `journaling.py` publish each trade row to the
Outbox, which delivers to a cloud store (S3, Google Sheets, etc.) that Lawrence controls.
The rolling file becomes a local cache only.

**Pros:**
- Durable by design — Outbox survives restarts; cloud store survives git operations
- No dependency on git for data persistence
- Cleanest long-term architecture

**Cons:**
- Requires deciding on a cloud store and implementing the Outbox → store adapter
- Non-trivial work (probably B-level effort, not trivially small)
- Lawrence must set up the cloud credentials and schema

**Verdict: right long-term direction; not an immediate fix. Scope as a separate backlog item if desired.**

---

## Recommendation

**Monday morning action:**

1. **Apply Option A immediately** — edit `.gitignore` and run `git rm --cached` on the
   three rolling files. This takes 2 minutes and prevents the next wipe.

2. **Consider Option C as a future B-item** if cloud-based trade storage becomes
   important for remote analysis or audit trails.

3. **Before any future `git reset` or `git checkout` on the live tree**, always verify
   `trades/trades.csv` is either (a) untracked, or (b) committed AND the daily snapshot
   files are current. The daily snapshots write at market close — they lag intraday by up
   to 8 hours.

---

## Data integrity note

`rejections.csv` and `excursions.csv` are also tracked (same hazard). The
`.gitignore` change should cover all three rolling files. The daily snapshot files
for excursions and rejections already survive git operations (they're untracked).
