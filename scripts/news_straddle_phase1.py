"""B83 Phase 1 -- cheap falsification of the news-event straddle (NO engine).

Simulates a pre-placed OCO straddle around each scheduled CPI/PPI/FOMC release
from the 1-min bars, with a realistic news-slippage cost model, and reports:
fill rate, same-bar whipsaw rate, and net expectancy in R per event type and per
slippage level. GO only if mean net expectancy > 0 at slip=4 ticks AND whipsaw
rate < 35% (B83 stop rule).

Bars are START-labeled (bar ts T covers [T, T+1); verified via Sunday-open and
RTH-open volume). All parameters are computed from bars with ts <= placement
(= event - place_lead_min); the event datetime is the only forward input and it
is a published schedule (no lookahead). Frozen holdout: calendar 2022 excluded.

Data-quality diagnostic: PPI/2025-Q4 dates are lower-confidence. For each event
we measure post-release vol expansion (window range / pre-event 5m ATR). A real
release expands vol; events with no expansion are likely mis-dated or no-reaction.
Expectancy is reported on ALL dated events (primary, honest) AND on the
expansion-confirmed subset (date-error robustness). Confirming on vol-expansion
does NOT select for straddle profit -- a big expansion can be a clean break (win)
or a whipsaw (loss); it only removes near-zero no-reaction events.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, asdict
from datetime import timedelta
from pathlib import Path

import pandas as pd

TICK = 0.25  # MNQ/NQ tick in points

# ---- fixed defaults (B83 spec) ----
PLACE_LEAD_MIN = 2
ENTRY_WINDOW_MIN = 15
OFFSET_ATR_MULT = 0.5
ATR_PERIOD = 14
TP_R = 1.0
MAX_HOLD_MIN = 60
SPREAD_TICKS = 1.0
EXPANSION_K = 1.5  # window_range / atr5 threshold to call an event "confirmed"


@dataclass
class EventResult:
    event_type: str
    ts_utc: str
    year: int
    atr5: float
    offset_x: float
    filled: bool
    side: str          # "long" | "short" | "" (no fill)
    whipsaw: bool       # same 1-min bar spanned both legs
    gap: bool
    outcome: str        # "tp" | "stop" | "timeout" | "whipsaw" | "nofill"
    r_mult: float       # realized R (0 if no fill)
    expansion_ratio: float


def load_bars(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["ts"])
    df = df.set_index("ts").sort_index()
    return df


def atr5_before(df: pd.DataFrame, placement) -> float | None:
    """5-min ATR(14) from 5-min bars fully closed at placement."""
    lo = placement - timedelta(minutes=5 * (ATR_PERIOD + 2) + 10)
    win = df.loc[lo:placement]
    if win.empty:
        return None
    # right-closed 5-min bars on the clock; keep only bars that close <= placement
    o = win["open"].resample("5min", label="left", closed="left").first()
    h = win["high"].resample("5min", label="left", closed="left").max()
    l = win["low"].resample("5min", label="left", closed="left").min()
    c = win["close"].resample("5min", label="left", closed="left").last()
    bars = pd.DataFrame({"open": o, "high": h, "low": l, "close": c}).dropna()
    # bar labeled T closes at T+5min; keep those with T+5 <= placement
    bars = bars[bars.index + timedelta(minutes=5) <= placement]
    if len(bars) < ATR_PERIOD + 1:
        return None
    bars = bars.iloc[-(ATR_PERIOD + 1):]
    prev_close = bars["close"].shift(1)
    tr = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - prev_close).abs(),
        (bars["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return float(tr.iloc[1:].mean())


def simulate_event(df: pd.DataFrame, event_type: str, ts, slip_ticks: float) -> EventResult | None:
    placement = ts - timedelta(minutes=PLACE_LEAD_MIN)
    atr5 = atr5_before(df, placement)
    if atr5 is None or atr5 <= 0:
        return None
    offset_x = OFFSET_ATR_MULT * atr5

    # ref = close of last fully-closed 1-min bar at placement = bar [P-1, P)
    ref_slice = df.loc[:placement - timedelta(minutes=1)]
    if ref_slice.empty:
        return None
    ref = float(ref_slice["close"].iloc[-1])

    buy_stop = ref + offset_x
    sell_stop = ref - offset_x

    # vol-expansion diagnostic over [E, E+15min]
    exp_win = df.loc[ts:ts + timedelta(minutes=ENTRY_WINDOW_MIN)]
    expansion_ratio = 0.0
    if not exp_win.empty:
        expansion_ratio = float(exp_win["high"].max() - exp_win["low"].min()) / atr5

    # entry window [P, P+ENTRY_WINDOW_MIN)
    win = df.loc[placement:placement + timedelta(minutes=ENTRY_WINDOW_MIN)]
    if win.empty:
        return None

    slip_pts = slip_ticks * TICK
    half_spread = (SPREAD_TICKS / 2.0) * TICK

    side = ""
    entry = stop_level = tp_level = None
    whipsaw = gap = False
    fill_idx = None

    for i in range(len(win)):
        bar = win.iloc[i]
        hi, lo, op = bar["high"], bar["low"], bar["open"]
        up = hi >= buy_stop
        dn = lo <= sell_stop
        if up and dn:
            # same 1-min bar spans both legs -> cannot resolve order (no sub-1min
            # data) -> whipsaw_loss: assume entered then stopped at opposite leg
            whipsaw = True
            side = "long"  # arbitrary; loss is symmetric (-(2X+2*slip))
            entry = buy_stop + slip_pts + half_spread
            stop_level = sell_stop  # opposite leg
            r_unit = entry - stop_level
            exit_px = sell_stop - slip_pts - half_spread
            r_mult = (exit_px - entry) / r_unit
            return EventResult(event_type, ts.isoformat(), ts.year, atr5, offset_x,
                               True, side, True, False, "whipsaw", float(r_mult),
                               expansion_ratio)
        if up:
            side = "long"
            gap = op >= buy_stop
            base = max(buy_stop, op) if gap else buy_stop
            entry = base + slip_pts + half_spread
            stop_level = sell_stop
            tp_level = entry + TP_R * (entry - stop_level)
            fill_idx = i
            break
        if dn:
            side = "short"
            gap = op <= sell_stop
            base = min(sell_stop, op) if gap else sell_stop
            entry = base - slip_pts - half_spread
            stop_level = buy_stop
            tp_level = entry - TP_R * (stop_level - entry)
            fill_idx = i
            break

    if side == "" or fill_idx is None:
        return EventResult(event_type, ts.isoformat(), ts.year, atr5, offset_x,
                           False, "", False, False, "nofill", 0.0, expansion_ratio)

    # manage from the fill bar forward, up to MAX_HOLD_MIN
    fill_ts = win.index[fill_idx]
    hold = df.loc[fill_ts:fill_ts + timedelta(minutes=MAX_HOLD_MIN)]
    r_unit = abs(entry - stop_level)
    outcome = "timeout"
    exit_px = None
    for j in range(len(hold)):
        bar = hold.iloc[j]
        hi, lo = bar["high"], bar["low"]
        if side == "long":
            hit_stop = lo <= stop_level
            hit_tp = hi >= tp_level
        else:
            hit_stop = hi >= stop_level
            hit_tp = lo <= tp_level
        if hit_stop and hit_tp:
            # ambiguous in-bar -> never assume favorable: stop first
            outcome = "stop"
            exit_px = (stop_level - slip_pts - half_spread) if side == "long" else (stop_level + slip_pts + half_spread)
            break
        if hit_stop:
            outcome = "stop"
            exit_px = (stop_level - slip_pts - half_spread) if side == "long" else (stop_level + slip_pts + half_spread)
            break
        if hit_tp:
            outcome = "tp"
            exit_px = (tp_level - half_spread) if side == "long" else (tp_level + half_spread)
            break
    if exit_px is None:  # timeout -> mark to last close
        exit_px = float(hold["close"].iloc[-1])
        if side == "long":
            exit_px -= half_spread
        else:
            exit_px += half_spread

    pnl = (exit_px - entry) if side == "long" else (entry - exit_px)
    r_mult = pnl / r_unit
    return EventResult(event_type, ts.isoformat(), ts.year, atr5, offset_x,
                       True, side, False, gap, outcome, float(r_mult), expansion_ratio)


def run(bars_path: str, events_path: str, slips: list[float], out_csv: str) -> None:
    df = load_bars(bars_path)
    bar_start, bar_end = df.index[0], df.index[-1]
    events = pd.read_csv(events_path, parse_dates=["ts_utc"])
    # in-coverage, exclude calendar 2022 (frozen holdout)
    events = events[(events.ts_utc >= bar_start + timedelta(hours=2)) &
                    (events.ts_utc <= bar_end - timedelta(hours=2)) &
                    (events.ts_utc.dt.year != 2022)]

    all_rows: list[dict] = []
    for slip in slips:
        results: list[EventResult] = []
        for _, ev in events.iterrows():
            r = simulate_event(df, ev.event_type, ev.ts_utc, slip)
            if r is not None:
                results.append(r)
        summarize(results, slip)
        if slip == slips[0]:
            for r in results:
                d = asdict(r); d["slip_ticks"] = slip; all_rows.append(d)

    if all_rows:
        out = Path(out_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            w.writeheader(); w.writerows(all_rows)
        print(f"\nper-event detail (slip={slips[0]}) -> {out}")


def _stats(rs: list[EventResult]) -> dict:
    n = len(rs)
    filled = [r for r in rs if r.filled]
    whips = [r for r in rs if r.whipsaw]
    traded_r = [r.r_mult for r in filled]
    all_r = [r.r_mult for r in rs]  # no-fill counts as 0
    wins = [r for r in filled if r.r_mult > 0]
    return {
        "n": n,
        "fill_rate": len(filled) / n if n else 0,
        "whipsaw_rate": len(whips) / len(filled) if filled else 0,
        "win_rate": len(wins) / len(filled) if filled else 0,
        "mean_R_traded": sum(traded_r) / len(traded_r) if traded_r else 0,
        "mean_R_all": sum(all_r) / n if n else 0,
        "total_R": sum(all_r),
    }


def summarize(results: list[EventResult], slip: float) -> None:
    print(f"\n========== slip_ticks_news = {slip} ==========")
    hdr = f"{'group':<16}{'n':>4}{'fill%':>7}{'whip%':>7}{'win%':>7}{'R/trade':>9}{'R/all':>8}{'totR':>8}"
    print(hdr)
    for et in ("CPI", "PPI", "FOMC"):
        s = _stats([r for r in results if r.event_type == et])
        print(f"{et:<16}{s['n']:>4}{s['fill_rate']*100:>7.0f}{s['whipsaw_rate']*100:>7.0f}"
              f"{s['win_rate']*100:>7.0f}{s['mean_R_traded']:>9.3f}{s['mean_R_all']:>8.3f}{s['total_R']:>8.1f}")
    s = _stats(results)
    print(f"{'ALL':<16}{s['n']:>4}{s['fill_rate']*100:>7.0f}{s['whipsaw_rate']*100:>7.0f}"
          f"{s['win_rate']*100:>7.0f}{s['mean_R_traded']:>9.3f}{s['mean_R_all']:>8.3f}{s['total_R']:>8.1f}")
    # expansion-confirmed subset (date-error robustness)
    conf = [r for r in results if r.expansion_ratio >= EXPANSION_K]
    sc = _stats(conf)
    print(f"{'ALL(conf>=%.1f)'%EXPANSION_K:<16}{sc['n']:>4}{sc['fill_rate']*100:>7.0f}{sc['whipsaw_rate']*100:>7.0f}"
          f"{sc['win_rate']*100:>7.0f}{sc['mean_R_traded']:>9.3f}{sc['mean_R_all']:>8.3f}{sc['total_R']:>8.1f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", default="bars/bars_MNQ_dbv_2021_2026.csv")
    ap.add_argument("--events", default="data/news_events.csv")
    ap.add_argument("--slips", default="2,4,6,8")
    ap.add_argument("--out", default="research/news_straddle_phase1.csv")
    a = ap.parse_args()
    run(a.bars, a.events, [float(x) for x in a.slips.split(",")], a.out)
