You are triaging a table of statistically unusual intraday futures sessions (NQ, ES, GC) for a systematic research pipeline. Your job is NOT to propose a trading rule — it is to pick which sessions are worth a colleague's time to reason about as the seed of a falsifiable hypothesis.

Each session below already carries a regime label from an independent classification pass (scheduled_macro, quarterly_event, idiosyncratic_shock, or none_identified) and a set of feature values describing how that session's price action diverged from its own recent history.

Sessions:
{sessions_json}

Select up to {max_shortlist} sessions. Prefer:
- a clear, specific regime label over none_identified (a labelled mechanism is more falsifiable than an unexplained anomaly)
- diversity across instruments and regime categories over repeatedly picking the single highest anomaly_score
- a session whose feature pattern suggests a repeatable structural cause, not a one-off idiosyncratic event unlikely to recur

Respond with ONLY a JSON object of this exact shape, referencing only anomaly_id values that appear above:
{{"shortlist": [{{"anomaly_id": "<string>", "rationale": "<one sentence: why this session>"}}]}}
