title: Gate A- signals on fib >= 1.0x to prevent morning DLL
priority: high
status: open
category: strategy
created: 2026-06-08
---
Finding from 2026-06-08 session: fib < 1.0x on A- grade is the morning loss driver. The DLL lockout on 06-08 would have been prevented by gating A- entries on fib >= 1.0x. A+ signals are unaffected (they already clear this naturally). Gate only applies to A- grade. Needs ablation test on backtest data before enabling live.
