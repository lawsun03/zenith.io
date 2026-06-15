import json, pathlib
path = pathlib.Path("research/findings.json")
data = json.loads(path.read_text(encoding="utf-8"))
entry = {
  "ts": "2026-06-14T10:55Z",
  "session": "wk4-b65",
  "item": "B65",
  "variant": "markov-2-regime-filter-phase1",
  "objective": "combine",
  "metrics": {
    "ifvg_long_bull_pf": 1.145,
    "ifvg_long_bear_pf": 0.896,
    "ifvg_long_ratio": 1.28,
    "ifvg_short_bear_pf": 0.566,
    "ifvg_short_bull_pf": 1.110,
    "ifvg_short_ratio": 0.51,
    "orb_long_bull_pf": 1.234,
    "orb_long_bear_pf": 1.341,
    "orb_long_ratio": 0.92,
    "orb_short_bear_pf": 0.611,
    "orb_short_bull_pf": 0.995,
    "orb_short_ratio": 0.61,
    "fix1_bull_stickiness_overlap": 0.83,
    "fix1_bull_stickiness_stride": 0.21,
    "stride_windows_total": 77,
    "stride_bull_windows": 15,
    "stride_sideways_windows": 52,
    "stride_bear_windows": 10,
    "phase1_go": False
  },
  "verdict": "rejected",
  "learned": (
      "Stride-sampled Markov 2.0 regime signal (20d non-overlapping windows, walk-forward "
      "point-in-time) does not materially separate NQ 5min trade quality: iFVG longs "
      "BULL/BEAR ratio=1.28x (below 1.3x threshold, 1/5 years consistent), ORB longs "
      "BACKWARD (bear-regime PF 1.34 > bull-regime 1.23). Shorts direction backward for "
      "both engines. FIX 1 confirms the true persistence is weak (BULL stickiness 0.83 "
      "overlapping vs 0.21 stride); labels are directionally correct but conservative "
      "thresholds yield 67% SIDEWAYS days. Joins B35/B5 in failed daily-context set."
  ),
  "links": {"doc": None, "runs": [], "src": None}
}
data.append(entry)
path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"findings.json now has {len(data)} entries, last: {data[-1]['item']}")
