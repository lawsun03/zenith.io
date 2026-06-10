title: Parameterize swing-point lookback (left/right candles)
priority: medium
status: open
category: backtest
created: 2026-06-08
---
TJR finding: swing-point definition (candles left/right) is a real tunable that materially changes which trades are taken. Tight = noise (too many false sweeps), loose = misses setups. Add left/right lookback as backtest sweep parameters. Include in the multi-instrument × variant Databento matrix.
