You are drafting ONE falsifiable intraday futures trading hypothesis, seeded by a specific anomalous session, for a research pipeline that pools evidence across NQ, ES and GC rather than fitting per instrument.

Seed session: {instrument} on {session_date}, regime label "{regime_label}". Why this session was shortlisted: {rationale}

Your hypothesis must have a stated causal MECHANISM — a specific, checkable reason this pattern should recur across all three instruments, not just an observation that it happened once. A mechanism like "price often reverses after a big move" is too vague to falsify; a mechanism must name the market behaviour it depends on and why that behaviour is structural rather than incidental to this one session.

Do not default to one style of strategy. Any mechanism family is fair game: trend-following (sma_7 crossing sma_21, price holding above or below vwap), mean reversion back to vwap or a moving average, opening-range or session-level breakouts, momentum (consecutive_closes, displacement), volatility expansion, time-of-day effects. ICT-style constructs (sweep_of, fvg) are ONE option among many, never the default. A hypothesis built only from cross_of / close_beyond / retrace_to against sma_7, sma_21 and vwap is exactly as welcome as one built from sweeps and gaps; pick whatever best fits the seed session's mechanism. sma_7 and sma_21 are simple moving averages of the 1-minute close; vwap is volume-weighted average price anchored at the strategy's session start each day. cross_of takes an optional level_b, in which case it fires when `level` crosses `level_b` (e.g. level sma_7, level_b sma_21, direction up = a bullish crossover).

A structural stop with anchor sweep_extreme only works when the entry contains a sweep_of; otherwise use stop.type atr or realised_sigma (or anchor entry_bar), or the strategy will never place a trade.

The rule must be expressed as a strategy IR document with this exact shape:
{{
  "ir_version": "1.0",
  "name": "<3-80 char slug-like name>",
  "instruments": ["NQ", "ES", "GC"],
  "session": {{"start": "HH:MM", "end": "HH:MM", "tz": "America/New_York"}},
  "entry": <predicate>,
  "exit": <predicate>,
  "stop": {{"type": "atr"|"realised_sigma"|"structural", "multiple": <0.25-5.0>, "lookback": <5-100>, "anchor": <optional>, "buffer": <optional>}},
  "target": {{"type": "atr"|"r_multiple"|"session_level", "multiple": <0.5-10.0>}},
  "sizing": {{"family": "micro"|"mini"|"full", "vol_target_annual": <0.05-0.40>, "max_contracts": <1-50>}}
}}

`instruments` must always be exactly ["NQ", "ES", "GC"] — this pipeline pools evidence across all three; there is no per-instrument parameter block. `entry` may compose only from these recognizable ops, each carrying "recognizable": true: {recognizable_ops}. Available level references (for ops that take a `level`/`level_b`): {levels}. `exit` may additionally use non-recognizable ops carrying "recognizable": false, on top of the same recognizable set. `stop.type` must be one of {stop_types}; `target.type` must be one of {target_types}; `sizing.family` must be one of {sizing_families}.

NEVER reference regime_label, account balance, daily loss limit, or trailing drawdown anywhere in the document — those are not inputs to rule design in this system.

Respond with ONLY a JSON object of this exact shape:
{{"mechanism": "<2-4 sentences: the causal story, stated precisely enough that a skeptic could argue against it>", "ir": <the IR document above>}}
