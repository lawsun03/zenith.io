# Research Journal — append-only. Newest entries at the BOTTOM.

Entry format:

## <ISO timestamp> — session <short-id> — <BACKLOG item or RESEARCH>
- **Ran:** <what was executed, exact commands/configs>
- **Numbers:** <headline metrics vs baseline>
- **Verdict:** candidate | rejected | dataset | shipped | partial
- **Learned:** <2 sentences, plain English — this line feeds the UI>
- **Next:** <pointer for the following session>

---

## 2026-06-12T22:30Z — session seed — infrastructure
- **Ran:** equity_export.py bridge built + verified (ORB r2.5 on 2024 →
  funded_sim: 26 attempts/10 passes/15 busts; XFA $23,566 net, 8/9 accounts
  busted at haircut $200, risk 1.25%).
- **Numbers:** first-ever funded-objective datapoint; bust rate at full sizing
  is the obvious frontier variable.
- **Verdict:** dataset
- **Learned:** The XFA payout pipeline is rich even from a config that only
  passes 24% of Combine months — and bust rate, not payout size, is the thing
  to optimize. Sizing is the lever (B1 probes it).
- **Next:** B1 — full funded-objective re-scoring of the bench.
