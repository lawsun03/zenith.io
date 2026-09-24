You are enumerating parameter variants around a trading hypothesis that survived adversarial review, for a walk-forward parameter-plateau check. This checks whether the rule's edge is robust across a neighbourhood of parameters, or a fluke tuned to one exact setting — so the variants must be genuine neighbours, not a redesign.

Mechanism: {mechanism}
Base IR document:
{base_ir_json}

Enumerate up to {max_variants} variants by sweeping ONLY numeric thresholds already present in the base document (e.g. stop.multiple, target.multiple, stop.lookback, entry min_atr/min_offset, session start/end) within the same schema bounds the base document respects. Do NOT:
- change `instruments` (always pooled across NQ, ES and GC)
- change the predicate structure or operators used in `entry`/`exit`
- reference regime_label, account balance, daily loss limit, or trailing drawdown
- add a per-instrument parameter block

Each variant must be a complete, standalone IR document of the same shape as the base document.

Respond with ONLY a JSON object of this exact shape:
{{"param_grid": {{"<field path>": [<swept values>], ...}}, "variants": [<complete IR document>, ...]}}
