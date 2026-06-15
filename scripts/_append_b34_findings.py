import json
from pathlib import Path

p = Path(__file__).parent.parent / "research" / "findings.json"
data = json.loads(p.read_text())

new_entry = {
    "ts": "2026-06-14T08:30:00Z",
    "session": "wk2-b34",
    "item": "B34",
    "variant": "sweep_bos Phase 1 -- OTE retrace bucket analysis (MNQ 5min, 5y excl 2022)",
    "objective": "infra",
    "metrics": {
        "bos_signals_5y": 16522,
        "stopped_in_retrace_pct": 56.0,
        "ote_bucket_n": 1262,
        "ote_wr": 0.371,
        "ote_pf": 2.06,
        "no_retrace_wr": 0.844,
        "no_retrace_pf": 18.90,
        "shallow_retrace_wr": 0.623,
        "shallow_retrace_pf": 5.79,
        "phase1_verdict": "NO-GO"
    },
    "verdict": "rejected",
    "learned": (
        "OTE retrace depth is inversely correlated with BOS forward performance on NQ 5min: "
        "no-retrace bucket (WR 84.4%, PF 18.90) strictly dominates OTE bucket (WR 37.1%, PF 2.06). "
        "56% of BOS events see price return through the sweep extreme (stop) within 20 bars -- "
        "the stop anchor is at the wrong level for BOS entries, same root cause as B33's probe rejection."
    ),
    "links": {
        "doc": None,
        "runs": [],
        "src": "youtu.be/ZqPEuatIYMc"
    }
}

data.append(new_entry)
p.write_text(json.dumps(data, indent=2))
print("OK -- entry appended, total", len(data))
