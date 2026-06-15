"""
Cross-engine validation: our backtest stack vs backtesting.py (kernc).

Runs the ORB strategy through BOTH engines on the IDENTICAL 5-min bar
sequence and diffs the trade lists. Signal logic is mirrored exactly on
purpose — what's under test is the independent fill / bracket / P&L
machinery (entry fills, stop/target touch detection, intrabar policy,
equity arithmetic), not the strategy.

Alignment used (must match on both sides):
  - entries fill at the signal bar's CLOSE (trade_on_close=True vs our
    PaperBroker market-at-last-close)
  - slippage 0, commission 0, fixed size 1, no partials
  - our risk limits OFF (enforce_risk_limits=False); flatten 15:05 CT and
    entry cutoff 14:30 CT replicated in the reference strategy
  - brackets first evaluated on the bar AFTER entry (both engines)

Known semantic difference to watch: same-bar stop+target whipsaws — ours
fills the stop (pessimistic); backtesting.py picks by its own internal
order. Diffs on such bars are expected and reported separately.

Usage:
    python scripts/validate_backtest_engine.py --bars bars/bars_MNQ_train_2024.csv
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd

from app.backtest.runner import BacktestConfig, run_backtest
from app.bot_config import StrategyParams
from app.replay import load_bars_csv

ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")

ORB_OPEN = dtime(9, 30)
ORB_RANGE_MIN = 15
ORB_R = Decimal("2.5")
CUTOFF_CT = dtime(14, 30)
FLATTEN_CT = dtime(15, 5)


def run_ours(bars):
    sp = StrategyParams(engine="orb", orb_r_multiple=ORB_R,
                        orb_range_minutes=ORB_RANGE_MIN, orb_open_et="09:30")
    cfg = BacktestConfig(
        instrument="MNQ",
        bars=iter(bars),
        timeframe="5min",
        contracts=1,
        risk_per_trade_pct=Decimal("0"),     # fixed 1 contract
        partial_profit_r=Decimal("0"),
        slippage_ticks_market=0,
        commission_per_side=Decimal("0"),
        strategy_params=sp,
        enforce_risk_limits=False,           # no MLL/DLL/DPL governor
    )
    result = asyncio.run(run_backtest(cfg))
    out = []
    for t in result.trades:
        out.append({
            "entry_ts": pd.Timestamp(t["entry_ts"]),
            "exit_ts": pd.Timestamp(t["exit_ts"]),
            "side": t["side"],
            "entry": float(t["entry_price"]),
            "exit": float(t["exit_price"]),
        })
    return out


def run_reference(bars):
    from backtesting import Backtest, Strategy

    df = pd.DataFrame({
        "Open": [float(b.open) for b in bars],
        "High": [float(b.high) for b in bars],
        "Low": [float(b.low) for b in bars],
        "Close": [float(b.close) for b in bars],
        "Volume": [float(b.volume) for b in bars],
    }, index=pd.DatetimeIndex([b.ts for b in bars]))

    class RefORB(Strategy):
        def init(self):
            self._day = None
            self._or_hi = None
            self._or_lo = None
            self._fired = False

        def next(self):
            ts = self.data.index[-1].to_pydatetime()
            et = ts.astimezone(ET)
            ct = ts.astimezone(CT)
            if et.date() != self._day:
                self._day = et.date()
                self._or_hi = self._or_lo = None
                self._fired = False

            # Flatten rule (engine parity): flat at/after 15:05 CT.
            if ct.time() >= FLATTEN_CT and self.position:
                self.position.close()

            start = et.replace(hour=ORB_OPEN.hour, minute=ORB_OPEN.minute,
                               second=0, microsecond=0)
            mins = (et - start).total_seconds() / 60
            hi = self.data.High[-1]
            lo = self.data.Low[-1]
            close = self.data.Close[-1]
            if mins < 0:
                return
            if mins < ORB_RANGE_MIN:
                self._or_hi = hi if self._or_hi is None else max(self._or_hi, hi)
                self._or_lo = lo if self._or_lo is None else min(self._or_lo, lo)
                return
            if self._or_hi is None or self._fired:
                return

            if close > self._or_hi:
                side, stop = "long", self._or_lo
            elif close < self._or_lo:
                side, stop = "short", self._or_hi
            else:
                return
            self._fired = True  # detector counts the breakout even if blocked

            # Entry cutoff + in-flatten-window block (engine parity).
            if ct.time() >= CUTOFF_CT:
                return
            if self.position:
                return  # engine would route to reversal; ORB solo is flat here

            r = abs(close - stop)
            if r == 0:
                return
            if side == "long":
                tp = close + float(ORB_R) * r
                self.buy(size=1, sl=stop, tp=tp)
            else:
                tp = close - float(ORB_R) * r
                self.sell(size=1, sl=stop, tp=tp)

    bt = Backtest(df, RefORB, cash=1_000_000, commission=0.0,
                  trade_on_close=True, exclusive_orders=False, finalize_trades=True)
    stats = bt.run()
    trades = stats["_trades"]
    out = []
    for _, t in trades.iterrows():
        out.append({
            "entry_ts": t["EntryTime"],
            "exit_ts": t["ExitTime"],
            "side": "long" if t["Size"] > 0 else "short",
            "entry": float(t["EntryPrice"]),
            "exit": float(t["ExitPrice"]),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", required=True)
    args = ap.parse_args()

    bars = list(load_bars_csv(args.bars, "MNQ", "5min"))
    print(f"bars: {len(bars)} 5-min bars from {args.bars}")

    ours = run_ours(bars)
    ref = run_reference(bars)
    print(f"\ntrades: ours={len(ours)} reference={len(ref)}")

    by_entry_ours = {(t['entry_ts'], t['side']): t for t in ours}
    by_entry_ref = {(t['entry_ts'], t['side']): t for t in ref}

    matched = exit_mismatch = 0
    only_ours = [k for k in by_entry_ours if k not in by_entry_ref]
    only_ref = [k for k in by_entry_ref if k not in by_entry_ours]
    pnl_ours = pnl_ref = 0.0
    for k, t in by_entry_ours.items():
        sgn = 1 if t["side"] == "long" else -1
        pnl_ours += sgn * (t["exit"] - t["entry"])
    for k, t in by_entry_ref.items():
        sgn = 1 if t["side"] == "long" else -1
        pnl_ref += sgn * (t["exit"] - t["entry"])

    for k in by_entry_ours:
        if k not in by_entry_ref:
            continue
        a, b = by_entry_ours[k], by_entry_ref[k]
        entry_ok = abs(a["entry"] - b["entry"]) < 1e-9
        exit_ok = abs(a["exit"] - b["exit"]) < 1e-9 and a["exit_ts"] == b["exit_ts"]
        if entry_ok and exit_ok:
            matched += 1
        else:
            exit_mismatch += 1
            print(f"  MISMATCH {k[0]} {k[1]}: "
                  f"ours entry={a['entry']:.2f} exit={a['exit']:.2f}@{a['exit_ts']} | "
                  f"ref entry={b['entry']:.2f} exit={b['exit']:.2f}@{b['exit_ts']}")

    print(f"\nexact matches (entry+exit price & time): {matched}")
    print(f"matched entry, divergent exit:            {exit_mismatch}")
    print(f"trades only in ours:                      {len(only_ours)}")
    for k in only_ours[:10]:
        print(f"   ours-only: {k}")
    print(f"trades only in reference:                 {len(only_ref)}")
    for k in only_ref[:10]:
        print(f"   ref-only:  {k}")
    print(f"\ntotal P&L (points, size 1): ours={pnl_ours:+.2f}  reference={pnl_ref:+.2f}  "
          f"diff={pnl_ours - pnl_ref:+.2f}")


if __name__ == "__main__":
    main()
