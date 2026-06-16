"""Forbes Model backtest harness + ablations (Task 8).

Measures the REAL edge of the Forbes Model (engine="forbes") over 5y MNQ 1-min
bars, EXCLUDING calendar-2022 (frozen holdout) from all headline numbers. The
author claims a 75-80% win rate; the published examples are post-hoc annotated
winners (selection bias), so the point of this script is to measure the truth.

Reuses the existing backtest machinery (no new backtester):
  - app.backtest.runner.run_backtest / BacktestConfig   (per-trade results)
  - app.backtest.funded_sim.{simulate_combines, simulate_xfa_chain,
    daily_pnls_from_equity}                              (funded pipeline)
  - app.replay.load_bars_csv                             (bars)
  - app.bot_config.{load_bot_config, strategy_for}       (StrategyParams)

Fill convention: PaperBroker's existing intrabar rule is used as-is (the same
rule every other engine in this repo is measured under). PaperBroker resolves a
bracket on the bar where the level is hit; on a bar that touches BOTH stop and
target it resolves stop-first (conservative). See app/broker/paper.py. We do not
override fill ordering — we report which convention applies.

Realized-R per trade = realized_pnl_usd / risk_usd, where
  risk_usd = stop_dist_points * dollars_per_point(MNQ=$2) * contracts.
stop_dist (entry->initial-stop, points) is attached to each trade by the runner.

Usage:  .venv/Scripts/python.exe scripts/run_forbes_backtest.py
"""
from __future__ import annotations

import asyncio
import csv
import statistics
import sys
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from app.backtest.runner import BacktestConfig, run_backtest
from app.backtest.funded_sim import (
    daily_pnls_from_equity,
    simulate_combines,
    simulate_xfa_chain,
)
from app.bot_config import load_bot_config, strategy_for
from app.replay import load_bars_csv

BARS_CSV = _REPO / "bars" / "bars_MNQ_dbv_2021_2026.csv"
INSTRUMENT = "MNQ"
TIMEFRAME = "1min"                     # Forbes is a 1-min engine (aggregates 1m->15m internally)
DOLLARS_PER_POINT = Decimal("2")       # MNQ: tick 0.25 @ $0.50/tick = $2/point
CONTRACTS = 1
EXCLUDE_YEAR = 2022                    # frozen holdout
YEAR_BUCKETS = ["2021", "2023", "2024", "2025-26"]
OUT_DIR = _REPO / "research" / "equity_forbes"

# 2025 and 2026 are pooled (2026 is a partial year).
def _bucket(year: int) -> str:
    if year in (2025, 2026):
        return "2025-26"
    return str(year)


def _load_bars():
    bars = load_bars_csv(str(BARS_CSV), INSTRUMENT, TIMEFRAME)
    return [b for b in bars if b.ts.year != EXCLUDE_YEAR]


def _make_strategy(bot_cfg, **overrides):
    s = strategy_for(bot_cfg, INSTRUMENT)
    update = {"engine": "forbes"}
    update.update(overrides)
    return s.model_copy(update=update)


async def _run(bars, strategy, label=""):
    cfg = BacktestConfig(
        instrument=INSTRUMENT,
        bars=bars,
        timeframe=TIMEFRAME,
        contracts=CONTRACTS,
        risk_per_trade_pct=Decimal("0"),     # fixed contracts (R is computed from stop_dist)
        partial_profit_r=Decimal("0"),       # no partials — measure raw RR distribution
        strategy_params=strategy,
        enforce_risk_limits=False,           # funded_sim applies its own rules
        label=label,
    )
    return await run_backtest(cfg)


def _trade_r(t: dict) -> float | None:
    """Realized R-multiple for a closed trade. None if stop_dist missing/zero."""
    sd = t.get("stop_dist")
    if sd in (None, "", "0"):
        return None
    stop_dist = Decimal(str(sd))
    if stop_dist == 0:
        return None
    size = Decimal(str(t.get("size", CONTRACTS)))
    risk_usd = stop_dist * DOLLARS_PER_POINT * size
    if risk_usd == 0:
        return None
    return float(Decimal(str(t["realized_pnl"])) / risk_usd)


def _metrics(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0}
    pnls = [float(t["realized_pnl"]) for t in trades]
    rs = [r for r in (_trade_r(t) for t in trades) if r is not None]
    wins = [p for p in pnls if p > 0]
    net = sum(pnls)
    # max drawdown on the realized equity curve (trade-ordered)
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    return {
        "n": n,
        "win_rate": 100.0 * len(wins) / n,
        "net_usd": net,
        "exp_usd": net / n,
        "exp_r": (statistics.fmean(rs) if rs else None),
        "p25_r": (statistics.quantiles(rs, n=4)[0] if len(rs) >= 2 else None),
        "p50_r": (statistics.median(rs) if rs else None),
        "p75_r": (statistics.quantiles(rs, n=4)[2] if len(rs) >= 2 else None),
        "max_dd_usd": max_dd,
    }


def _print_metrics_row(label: str, m: dict) -> None:
    if m["n"] == 0:
        print(f"  {label:<14} {'0 trades':>10}")
        return
    p25 = f"{m['p25_r']:+.2f}" if m["p25_r"] is not None else "  n/a"
    p50 = f"{m['p50_r']:+.2f}" if m["p50_r"] is not None else "  n/a"
    p75 = f"{m['p75_r']:+.2f}" if m["p75_r"] is not None else "  n/a"
    exr = f"{m['exp_r']:+.3f}" if m["exp_r"] is not None else "n/a"
    print(
        f"  {label:<14} n={m['n']:>4}  win={m['win_rate']:5.1f}%  "
        f"R[p25/p50/p75]={p25}/{p50}/{p75}  "
        f"exp={exr}R (${m['exp_usd']:+.0f})  net=${m['net_usd']:+.0f}  "
        f"maxDD=${m['max_dd_usd']:.0f}"
    )


def _write_trade_csv(path: Path, trades: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entry_ts", "exit_ts", "side", "size", "entry_price",
                    "exit_price", "pnl_usd", "stop_dist_pts", "realized_r"])
        for t in trades:
            r = _trade_r(t)
            w.writerow([
                t["entry_ts"], t["exit_ts"], t["side"], t.get("size", ""),
                t.get("entry_price", ""), t.get("exit_price", ""),
                t["realized_pnl"], t.get("stop_dist", ""),
                f"{r:.4f}" if r is not None else "",
            ])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print("FORBES MODEL BACKTEST — 5y MNQ 1-min (2022 holdout excluded)")
    print("=" * 78)
    print(f"bars: {BARS_CSV.name}  instrument={INSTRUMENT}  tf={TIMEFRAME}  "
          f"contracts={CONTRACTS}")
    print("fill rule: PaperBroker intrabar (stop-first on same-bar; repo default)")

    bot_cfg = load_bot_config(_REPO / "bot_config.json")
    base_strategy = _make_strategy(bot_cfg)
    print(f"\nDefault Forbes params: killzone={base_strategy.forbes_killzone_et} "
          f"or={base_strategy.forbes_or_open_et}+{base_strategy.forbes_or_minutes}m "
          f"min_fvgs={base_strategy.forbes_or_min_fvgs} "
          f"target_mode={base_strategy.forbes_target_mode} "
          f"min_rr={base_strategy.forbes_min_rr} "
          f"stop_mode={base_strategy.forbes_stop_mode} "
          f"max/day={base_strategy.forbes_max_trades_per_day}")

    # --- DEFAULT RUN ---
    bars = _load_bars()
    n_bars = len(bars)
    n_days = len({b.ts.astimezone().date() for b in bars}) if bars else 0
    print(f"\nLoaded {n_bars} bars (2022 excluded).")
    result = asyncio.run(_run(bars, base_strategy, label="default"))
    trades = result.trades
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _write_trade_csv(OUT_DIR / "forbes_default_trades.csv", trades)

    print("\n" + "-" * 78)
    print("HEADLINE — default config")
    print("-" * 78)
    overall = _metrics(trades)
    _print_metrics_row("OVERALL", overall)

    by_bucket: dict[str, list[dict]] = {b: [] for b in YEAR_BUCKETS}
    for t in trades:
        yr = int(str(t["entry_ts"])[:4])
        by_bucket.setdefault(_bucket(yr), []).append(t)
    for b in YEAR_BUCKETS:
        _print_metrics_row(b, _metrics(by_bucket.get(b, [])))

    # --- WIN-RATE vs AUTHOR CLAIM ---
    print("\n" + "-" * 78)
    print("WIN RATE vs AUTHOR'S 75-80% CLAIM")
    print("-" * 78)
    if overall["n"] == 0:
        print("  0 trades — no win rate to compare (see 0-trade diagnostics below).")
    else:
        wr = overall["win_rate"]
        verdict = ("MATCHES" if 75 <= wr <= 80
                   else ("BELOW" if wr < 75 else "ABOVE"))
        print(f"  Measured win rate = {wr:.1f}%  ->  {verdict} the 75-80% claim.")
        if wr < 75:
            print("  The claim is NOT supported empirically — the published winners "
                  "are selection-biased.")

    # --- 0-TRADE DIAGNOSTICS (always print the funnel; cheap + informative) ---
    print("\n" + "-" * 78)
    print("ENTRY FUNNEL DIAGNOSTICS (re-run with instrumentation)")
    print("-" * 78)
    _diagnostics(bars_factory=_load_bars, strategy=base_strategy)

    if overall["n"] == 0:
        print("\n*** 0 TRADES on default config. Funnel above localizes where setups "
              "die. Reporting as a real finding — no fake trades. ***")

    # --- ABLATIONS ---
    print("\n" + "=" * 78)
    print("ABLATIONS")
    print("=" * 78)

    def ablate(label, **ov):
        s = _make_strategy(bot_cfg, **ov)
        res = asyncio.run(_run(_load_bars(), s, label=label))
        _print_metrics_row(label, _metrics(res.trades))
        return res

    print("\n(a) Killzone gate:")
    _print_metrics_row("default KZ", overall)
    ablate("KZ off", forbes_killzone_et="00:00-23:59")

    print("\n(b) FVG-present trade-days (stand-aside rate):")
    _print_fvg_standaside(_load_bars(), base_strategy)

    print("\n(c) target_mode (or_top/midway_poi are unimplemented -> expect 0):")
    for mode in ("liquidity", "or_top", "midway_poi"):
        ablate(f"tgt={mode}", forbes_target_mode=mode)

    print("\n(d) min_rr sweep:")
    for rr in ("1.0", "1.4", "2.0"):
        ablate(f"min_rr={rr}", forbes_min_rr=Decimal(rr))

    print("\n(e) stop_mode:")
    for sm in ("beyond_wick", "beyond_or"):
        ablate(f"stop={sm}", forbes_stop_mode=sm)

    # --- FUNDED CHECK ---
    print("\n" + "=" * 78)
    print("FUNDED PIPELINE (default config; haircut 0/200/400)")
    print("=" * 78)
    curve = result.stats.equity_curve
    if not curve:
        print("  No equity curve (0 trades) -> no combine passes, no XFA payouts.")
    else:
        daily = daily_pnls_from_equity(curve)
        print(f"  trading days with P&L: {len(daily)}")
        for hc in (0, 200, 400):
            c = simulate_combines(daily, haircut=Decimal(str(hc)))
            x = simulate_xfa_chain(daily, haircut=Decimal(str(hc)))
            net = float(x["net_payouts"])
            print(f"  haircut ${hc:>3}: combine {c['passes']}/{c['attempts']} pass "
                  f"({c['busts']} bust) | XFA {x['accounts']} acct, {x['busts']} bust, "
                  f"net payout ${net:,.0f}")

    print("\nDone.")
    return 0


def _diagnostics(bars_factory, strategy) -> None:
    """Re-run the detector standalone, counting where setups die. Pure read of
    detector state — does not place orders, so it isolates the entry funnel from
    the engine/broker. Mirrors the live ForbesDetector.on_bar pipeline exactly."""
    from app.strategy.forbes import ForbesConfig, ForbesDetector
    from app.strategy.killzone import ET

    det = ForbesDetector(ForbesConfig.from_params(INSTRUMENT, strategy))
    days_seen = set()
    days_or_locked = set()
    days_eligible = set()            # OR locked AND >= min_fvgs
    bars_in_kz_eligible = 0
    signals = 0
    sig_days = set()

    for bar in bars_factory():
        et_date = bar.ts.astimezone(ET).date()
        days_seen.add(et_date)
        sig = det.on_bar(bar)
        if det._or_locked:
            days_or_locked.add(et_date)
        if det.day_eligible():
            days_eligible.add(et_date)
            if det.in_killzone(bar.ts):
                bars_in_kz_eligible += 1
        if sig is not None:
            signals += 1
            sig_days.add(et_date)

    nd = len(days_seen) or 1
    print(f"  trading days:                 {len(days_seen)}")
    print(f"  days OR locked:               {len(days_or_locked)} "
          f"({100*len(days_or_locked)/nd:.0f}%)")
    print(f"  days eligible (OR+min_fvgs):  {len(days_eligible)} "
          f"({100*len(days_eligible)/nd:.0f}%)  <- stand-aside rate "
          f"{100*(1-len(days_eligible)/nd):.0f}%")
    print(f"  bars in killzone on elig days:{bars_in_kz_eligible}")
    print(f"  signals emitted (detector):   {signals} on {len(sig_days)} days")
    if signals == 0 and len(days_eligible) > 0:
        print("  -> Days ARE eligible but NO triggers fire: entry geometry "
              "(iFVG/sweep/breakout + min_rr + unswept target) is the bottleneck.")
    elif len(days_eligible) == 0:
        print("  -> NO eligible days: OR-FVG day-gate (or_min_fvgs) stands aside "
              "on every day. The day-eligibility gate is the bottleneck.")


def _print_fvg_standaside(bars, strategy) -> None:
    from app.strategy.forbes import ForbesConfig, ForbesDetector
    from app.strategy.killzone import ET

    det = ForbesDetector(ForbesConfig.from_params(INSTRUMENT, strategy))
    days = set()
    traded_days = set()        # eligible (FVG-present) days
    for bar in bars:
        d = bar.ts.astimezone(ET).date()
        days.add(d)
        det.on_bar(bar)
        if det.day_eligible():
            traded_days.add(d)
    nd = len(days) or 1
    print(f"  total days={len(days)}  FVG-present (eligible) days={len(traded_days)} "
          f"({100*len(traded_days)/nd:.0f}%)  stand-aside={100*(1-len(traded_days)/nd):.0f}%")


if __name__ == "__main__":
    raise SystemExit(main())
