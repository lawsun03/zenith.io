"""Defining-behavior tests for the edge-localization diagnostic.

Encodes WHY the tool exists: it must (1) FIND a robust sub-edge when the
aggregate is dead but one bucket is consistently good, (2) NOT cry edge when
everything is uniformly bad, and (3) REJECT a bucket whose high PF is concentrated
in a single year (the anti-overfit year-consistency guard). If any of these flip,
the diagnostic is unsafe for the research loop.
"""
import importlib.util
from pathlib import Path

import pandas as pd

_spec = importlib.util.spec_from_file_location(
    "edge_diagnostics", Path(__file__).resolve().parents[1] / "scripts" / "edge_diagnostics.py")
ed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ed)


def _rows(side, year, wins, win_r, losses, loss_r, **extra):
    out = []
    for _ in range(wins):
        out.append({"side": side, "year": year, "r": win_r, **extra})
    for _ in range(losses):
        out.append({"side": side, "year": year, "r": loss_r, **extra})
    return out


def test_localizes_robust_subedge_when_aggregate_is_dead():
    rows = []
    for y in (2023, 2024, 2025):                       # long: PF 2.0, positive every year
        rows += _rows("long", y, 7, 2.0, 7, -1.0)
        rows += _rows("short", y, 3, 1.0, 11, -1.0)    # short: PF 0.27, negative every year
    df = pd.DataFrame(rows)
    res = ed.localize(df, ["side"], pf_min=1.2, min_n=30, verbose=False)
    by = res["side"].set_index("side")
    assert bool(by.loc["long", "robust"]) is True       # the hidden edge is found
    assert bool(by.loc["short", "robust"]) is False     # the loser is not a hit


def test_no_false_edge_when_uniformly_negative():
    rows = []
    for y in (2023, 2024, 2025):
        rows += _rows("long", y, 0, 1.0, 14, -1.0)
        rows += _rows("short", y, 0, 1.0, 14, -1.0)
    df = pd.DataFrame(rows)
    res = ed.localize(df, ["side"], pf_min=1.2, min_n=30, verbose=False)
    assert not res["side"]["robust"].any()              # genuine reject, no buckets flagged


def test_year_consistency_guard_rejects_single_year_spike():
    # Bucket A has a high aggregate PF but it ALL comes from 2023.
    rows = []
    rows += _rows("x", 2023, 20, 3.0, 0, -1.0, zone="A")   # 2023 huge
    rows += _rows("x", 2024, 0, 1.0, 10, -1.0, zone="A")   # 2024 negative
    rows += _rows("x", 2025, 0, 1.0, 10, -1.0, zone="A")   # 2025 negative
    df = pd.DataFrame(rows)
    res = ed.localize(df, ["zone"], pf_min=1.2, min_n=30, verbose=False)
    a = res["zone"].set_index("zone").loc["A"]
    assert a["pf"] > 1.2                                  # aggregate PF looks great...
    assert bool(a["robust"]) is False                    # ...but only 1/3 years -> NOT robust
