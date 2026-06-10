title: Multi-symbol backtest UI parity
priority: low
status: open
category: ui
created: 2026-06-08
---
The backtests page UI still uses the old __main__.py loop path. It needs to delegate to runner.run_backtest() to support the multi-instrument design (MGC+MNQ+MES). The UI should be able to trigger and display results for all three instruments in one run, matching the live trading multi-instrument setup.
