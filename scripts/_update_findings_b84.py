"""One-shot: append B84 entry to findings.json."""
import json

with open("research/findings.json", encoding="utf-8") as f:
    data = json.load(f)

new_entry = {
    "ts": "2026-06-14T21:50:00Z",
    "session": "wk6-b84",
    "item": "B84",
    "variant": "phase-1a news-day suppression analysis (iFVG + ORB on CPI/PPI/FOMC days)",
    "objective": "funded",
    "metrics": {
        "ifvg_news_pf": 0.890,
        "ifvg_nonnews_pf": 0.988,
        "ifvg_ratio": 1.111,
        "orb_news_pf": 1.167,
        "orb_nonnews_pf": 1.552,
        "orb_ratio": 1.330,
        "go_threshold": 1.30,
        "ifvg_news_trades_5y": 290,
        "orb_news_trades_5y": 109,
    },
    "verdict": "rejected",
    "learned": (
        "iFVG news-day ratio 1.111 < 1.30 GO threshold and non-monotonic (inverted 2021/2023). "
        "ORB ratio exactly 1.330 but news-day ORB PF=1.167 still positive; FOMC is ORBs strongest "
        "ORB subset (PF=1.562). Suppressing positive news-day ORB triggers volume starvation (Lesson 151). "
        "B83/B84 news-event research thread exhausted."
    ),
    "links": {"doc": None, "runs": [], "src": None},
}
data.append(new_entry)

with open("research/findings.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print(f"findings.json now has {len(data)} entries")
print(f"Last: {data[-1]['item']} | {data[-1]['verdict']}")
