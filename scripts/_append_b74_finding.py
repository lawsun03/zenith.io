import json
from pathlib import Path

p = Path("research/findings.json")
data = json.loads(p.read_text("utf-8"))

entry = {
    "ts": "2026-06-14T16:00Z",
    "session": "wk5-b74",
    "item": "B74",
    "variant": "per-hour iFVG PF audit + block_9/block_6_9",
    "objective": "combine",
    "metrics": {
        "phase1_go_hours_ifvg": [6, 9, 13, 21],
        "hour9_pf": 0.487,
        "hour9_years_negative": 5,
        "hour6_pf": 0.578,
        "hour6_years_negative": 3,
        "block9_passes": 44,
        "block9_npm": 564,
        "block9_sust": 3.38,
        "block69_passes": 41,
        "block69_npm": 560,
        "block69_sust": 3.15,
        "b57_reference_npm": 566,
        "b57_reference_sust": 3.54,
    },
    "verdict": "rejected",
    "learned": (
        "Hour 9ET (09:00-10:00, pre-RTH) is the most consistently loss-making iFVG hour "
        "in deployed close-mode config (PF=0.487, 5/5 years). Blocking it improves over B42 "
        "(+$15/mo, +0.15x sust) but both block variants lose vs B57 on both metrics (stop rule fires). "
        "B57 r=2.5 change is more impactful than any per-hour block."
    ),
    "links": {
        "doc": None,
        "runs": ["equity_b74/", "mfe_mae_deployed_close.csv"],
        "src": None,
    },
}

data.append(entry)
p.write_text(json.dumps(data, indent=1), "utf-8")
print("appended B74 finding")
