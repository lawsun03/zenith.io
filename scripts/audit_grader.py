"""
B56 — SetupGrader per-component audit.

For each grader component (mom/tgt/fvg/pd/del/fib), computes WR, PF, and
mean-R split by component value (True vs False for booleans; three buckets
for fib extension: <1.0, [1.0,1.5), >=1.5 matching the grader's score tiers).

GO/NO-GO decision:
  - If ANY component shows a meaningful monotone split (PF ratio True/False >= 1.2
    or >= 1.3 for the recommended keep/refactor threshold), flag it as PREDICTIVE.
  - If NO component splits, recommend removing the grader gate (it adds complexity
    with no predictive value).
  - If a component is INVERTED (True PF < False PF), that's a miscalibration bug
    -- flag as BUG.

Output: component-level table, then overall verdict.
"""
from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.runner import BacktestConfig, BacktestResult, run_backtest
from app.bot_config import load_bot_config, strategy_for
from app.replay import load_bars_csv

YEARS = [2021, 2023, 2024, 2025, 2026]  # exclude 2022 holdout
BARS_DIR = _REPO_ROOT / "bars" / "yearly"
BOT_CFG_PATH = _REPO_ROOT / "bot_config.json"

# Grader scoring thresholds for fib_extension (from grader.py)
FIB_BUCKETS = [
    ("<1.0",   lambda f: f < Decimal("1.0")),
    ("[1.0,1.5)", lambda f: Decimal("1.0") <= f < Decimal("1.5")),
    (">=1.5",  lambda f: f >= Decimal("1.5")),
]

BOOL_COMPONENTS = ["mom", "tgt", "fvg", "pd", "del"]
COMPONENT_LABELS = {
    "mom": "Momentum (body_to_atr>=1.0)",
    "tgt": "Target clear",
    "fvg": "FVG singular",
    "pd": "Premium/Discount ok",
    "del": "Delivery FVG present",
    "fib": "Fib extension tier",
}


def _empty_bucket() -> dict:
    return {"n": 0, "wins": 0, "gross_win": Decimal(0), "gross_loss": Decimal(0), "r_sum": Decimal(0)}


def _run_year(year: int, strategy, bot_cfg) -> list[dict]:
    bars_path = BARS_DIR / f"bars_MNQ_dbv_{year}.csv"
    bars = load_bars_csv(str(bars_path), "MNQ", "5min")
    cfg = BacktestConfig(
        instrument="MNQ",
        bars=bars,
        timeframe="5min",
        contracts=1,
        risk_per_trade_pct=Decimal("1.0"),
        partial_profit_r=Decimal("0"),
        enabled_killzones=["all"],
        strategy_params=strategy,
        enforce_risk_limits=False,
    )
    result: BacktestResult = asyncio.run(run_backtest(cfg))
    return result.trades


def _accum(bucket: dict, pnl: Decimal) -> None:
    bucket["n"] += 1
    if pnl > 0:
        bucket["wins"] += 1
        bucket["gross_win"] += pnl
    else:
        bucket["gross_loss"] += abs(pnl)


def _pf(b: dict) -> str:
    if b["gross_loss"] == 0:
        return "inf" if b["gross_win"] > 0 else "N/A"
    return f"{float(b['gross_win'] / b['gross_loss']):.3f}"


def _wr(b: dict) -> str:
    if b["n"] == 0:
        return "N/A"
    return f"{100 * b['wins'] / b['n']:.1f}%"


def _net(b: dict) -> str:
    return f"{float(b['gross_win'] - b['gross_loss']):+,.0f}"


def _ratio(b_true: dict, b_false: dict) -> str:
    if b_false["gross_loss"] == 0 or b_true["gross_loss"] == 0:
        return "N/A"
    pf_t = b_true["gross_win"] / b_true["gross_loss"]
    pf_f = b_false["gross_win"] / b_false["gross_loss"]
    if pf_f == 0:
        return "N/A"
    return f"{float(pf_t / pf_f):.3f}"


def _verdict(b_true: dict, b_false: dict) -> str:
    """Classify a boolean component as PREDICTIVE/INVERTED/NOISE."""
    if b_true["gross_loss"] == 0 or b_false["gross_loss"] == 0 or b_true["n"] < 5 or b_false["n"] < 5:
        return "INSUFFICIENT DATA"
    pf_t = b_true["gross_win"] / b_true["gross_loss"]
    pf_f = b_false["gross_win"] / b_false["gross_loss"]
    if pf_f == 0:
        return "INSUFFICIENT DATA"
    ratio = pf_t / pf_f
    if ratio >= Decimal("1.2"):
        return "PREDICTIVE" if ratio >= Decimal("1.3") else "WEAK-PREDICTIVE"
    if ratio < Decimal("0.85"):
        return "INVERTED (BUG?)"
    return "NOISE"


def main() -> None:
    bot_cfg = load_bot_config(BOT_CFG_PATH)
    strategy = strategy_for(bot_cfg, "MNQ")

    overrides = {
        "engine": "ifvg",
        "ifvg_entry_mode": "close",
        "min_absolute_body": Decimal("5.0"),
        "stop_buffer": Decimal("3.0"),
        "r_multiple": Decimal("3.5"),
        "allowed_sides": "long",
        "swing_stop_lookback": 0,
        "target_clarity_mode": "off",
        "grader_min_grade": "F",          # accept all trades for this audit
    }
    for k, v in overrides.items():
        strategy = strategy.model_copy(update={k: v})

    # Accumulators: bool components — True vs False
    bool_stats: dict[str, dict[bool, dict]] = {
        c: {True: _empty_bucket(), False: _empty_bucket()}
        for c in BOOL_COMPONENTS
    }
    # Fib: three buckets
    fib_stats: dict[str, dict] = {label: _empty_bucket() for label, _ in FIB_BUCKETS}

    total_trades = 0
    ungraded = 0

    for year in YEARS:
        trades = _run_year(year, strategy, bot_cfg)
        for t in trades:
            criteria = t.get("criteria")
            if criteria is None:
                ungraded += 1
                continue
            pnl = Decimal(str(t["realized_pnl"]))
            total_trades += 1

            for c in BOOL_COMPONENTS:
                val = criteria.get(c)
                if isinstance(val, bool):
                    _accum(bool_stats[c][val], pnl)

            fib_val = Decimal(str(criteria.get("fib", "0")))
            for label, fn in FIB_BUCKETS:
                if fn(fib_val):
                    _accum(fib_stats[label], pnl)
                    break

        print(f"  Year {year}: {len(trades)} trades")

    print(f"\nTotal graded trades: {total_trades}  (ungraded/ORB: {ungraded})")
    print("\n=== Per-Component Audit (deployed iFVG config, 5y excl 2022) ===\n")

    summary = []
    for c in BOOL_COMPONENTS:
        label = COMPONENT_LABELS[c]
        bt = bool_stats[c][True]
        bf = bool_stats[c][False]
        v = _verdict(bt, bf)
        summary.append((c, v))
        print(f"--- {label} ({c}) ---")
        print(f"  True : n={bt['n']:4d}  WR={_wr(bt):>7}  PF={_pf(bt):>8}  net={_net(bt):>10}")
        print(f"  False: n={bf['n']:4d}  WR={_wr(bf):>7}  PF={_pf(bf):>8}  net={_net(bf):>10}")
        print(f"  PF ratio (True/False): {_ratio(bt, bf)}  -> {v}\n")

    # Fib extension
    label = COMPONENT_LABELS["fib"]
    print(f"--- {label} (fib) ---")
    prev_pf: float | None = None
    monotone = True
    fib_pfs = []
    for bucket_label, _ in FIB_BUCKETS:
        b = fib_stats[bucket_label]
        pf_val = float(b["gross_win"] / b["gross_loss"]) if b["gross_loss"] > 0 else None
        fib_pfs.append(pf_val)
        print(f"  {bucket_label:12s}: n={b['n']:4d}  WR={_wr(b):>7}  PF={_pf(b):>8}  net={_net(b):>10}")
        if prev_pf is not None and pf_val is not None and pf_val < prev_pf:
            monotone = False
        if pf_val is not None:
            prev_pf = pf_val
    fib_verdict = "PREDICTIVE (monotone)" if monotone and all(p is not None for p in fib_pfs) else "NON-MONOTONE/NOISE"
    print(f"  Monotone ascending: {monotone}  -> {fib_verdict}\n")
    summary.append(("fib", fib_verdict))

    print("=== Summary ===")
    predictive = [c for c, v in summary if "PREDICTIVE" in v]
    inverted = [c for c, v in summary if "INVERTED" in v or "BUG" in v]
    noise = [c for c, v in summary if "NOISE" in v or "INSUFFICIENT" in v]
    print(f"  Predictive  : {predictive if predictive else '(none)'}")
    print(f"  Inverted    : {inverted if inverted else '(none)'}")
    print(f"  Noise       : {noise if noise else '(none)'}")
    print()
    if inverted:
        print("VERDICT: BUG — inverted component(s) detected, fix before any gate use.")
    elif len(predictive) >= 1:
        print("VERDICT: REFACTOR — keep only predictive components, re-weight grader.")
    else:
        print("VERDICT: REMOVE GATE — no component predicts outcome; grader adds noise.")


if __name__ == "__main__":
    main()
