title: Rejection ledger — CSV + analytics page
priority: medium
status: open
category: observability
created: 2026-06-08
---
Every rejected signal (pretrade gate, grader, cooldown, HTF block) should be written to rejections.csv with reason code and signal context. The analytics page should show a breakdown of rejection reasons over time so patterns are visible (e.g. "40% of setups blocked by HTF bias").

Backend: append to rejections.csv on every OrderOutcome(placed=False). Frontend: add rejection breakdown chart to analytics page.
