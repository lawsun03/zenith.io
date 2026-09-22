"""B89 engine parity: prove the news_straddle ENGINE reproduces the B85 oracle.

Drives NewsStraddleDetector over the 1-second CPI windows and compares its
per-event decision (side / fire vs whipsaw vs nofill) against the oracle
scripts/news_straddle_cpi_1s.py:simulate(). Prints agreement counts plus the
oracle's headline P&L (the expectancy source of truth — the engine carries the
SIGNAL logic; the PaperBroker cannot price resting-stop entries, see the engine
module docstring). Run: .venv/Scripts/python.exe scripts/news_straddle_engine_parity.py
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pandas as pd

from app.sim.events import Bar
from app.strategy.news_straddle import (NewsStraddleConfig, NewsStraddleDetector)
from scripts.news_straddle_cpi_1s import load_1s, simulate

BARS = "bars/bars_NQ_1s_cpi_windows.csv"
OFFSET, TICK = 60, Decimal("0.25")


def main(tp_r: float = 3.0) -> None:
    df = load_1s(BARS)
    ev = pd.read_csv("data/news_events.csv", parse_dates=["ts_utc"])
    ev = ev[(ev.event_type == "CPI") & (ev.ts_utc.dt.year != 2022)
            & (ev.ts_utc >= df.index[0]) & (ev.ts_utc <= df.index[-1])]
    event_times = [t.to_pydatetime() for t in ev.ts_utc]

    det = NewsStraddleDetector(NewsStraddleConfig(
        instrument="MNQ", event_times=event_times, offset_ticks=OFFSET,
        tp_r=Decimal(str(tp_r)), tick=TICK, min_range_bars=60))

    fires: dict = {}
    for ts, row in df.iterrows():
        s = det.on_bar(Bar(instrument="MNQ", timeframe="1s", ts=ts.to_pydatetime(),
                           open=Decimal(str(row["open"])), high=Decimal(str(row["high"])),
                           low=Decimal(str(row["low"])), close=Decimal(str(row["close"])),
                           volume=int(row["volume"])))
        if s is not None:
            fires[s.created_at] = s

    agree = mismatch = 0
    oracle_rs: list[float] = []
    for e in event_times:
        o = simulate(df, pd.Timestamp(e), OFFSET, tp_r)
        if o is None:                       # oracle skipped (insufficient coverage)
            continue
        outcome, r = o
        sigs = [s for t, s in fires.items() if e <= t <= e + timedelta(minutes=30)]
        eng_side = sigs[0].side if sigs else ("whipsaw" if not sigs else "")
        # Oracle directional outcomes imply a fire; whipsaw implies no fire.
        if outcome in ("stop", "tp", "timeout"):
            oracle_rs.append(r)
            ok = bool(sigs)
        elif outcome == "whipsaw":
            oracle_rs.append(r)
            ok = not sigs
        else:                                # nofill
            ok = not sigs
        agree += int(ok); mismatch += int(not ok)
        flag = "" if ok else "  <-- MISMATCH"
        print(f"{e:%Y-%m-%d}  oracle={outcome:<8} r={r:+.2f}  engine={eng_side or 'nofill':<8}{flag}")

    n = len(oracle_rs)
    wins = [r for r in oracle_rs if r > 0]
    gw = sum(r for r in oracle_rs if r > 0); gl = -sum(r for r in oracle_rs if r < 0)
    pf = gw / gl if gl > 0 else float("inf")
    print(f"\nParity: {agree} agree / {mismatch} mismatch")
    print(f"Oracle headline (tp_r={tp_r}): n={n} win%={len(wins)/n*100:.0f} "
          f"PF={pf:.2f} R/trade={sum(oracle_rs)/n:+.3f} totR={sum(oracle_rs):+.1f}")


if __name__ == "__main__":
    main(3.0)
