"""
B47 benchmark: iFVG×ORB directional confluence gate on combined engine.

Two variants vs the deployed combined-engine baseline (5y excl 2022):
  1. baseline — engine=combined, deployed params, no gate
  2. gate     — same + ifvg_orb_confluence_gate=True

Deployed MNQ overrides (from strategy_overrides in bot_config.json):
  stop_buffer=3.0, min_absolute_body=5.0, r_multiple=3.5, orb_r_multiple=2.5
  ifvg_entry_mode=close, swing_stop_lookback=30

Phase A (funded objective): run equity_export per year, stitch, funded_sim.
Phase B (combine objective): monthly combine pass rate on test 2025-2026.

Success criteria:
  Primary:   gate funded sust > baseline funded sust
  Secondary: gate combine pass rate >= baseline pass rate
Stop rule:   reject if BOTH sust AND pass rate are worse than baseline.
"""
from __future__ import annotations

import asyncio
import csv
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.funded_sim import (
    daily_pnls_from_equity,
    simulate_combines,
    simulate_xfa_chain,
)
from app.backtest.runner import BacktestConfig, _trading_day_ct, run_backtest
from app.bot_config import load_bot_config, strategy_for
from app.replay import load_bars_csv

YEARS = ["2021", "2023", "2024", "2025", "2026"]
BARS_YEARLY = _REPO_ROOT / "bars" / "yearly"
BARS_TEST = _REPO_ROOT / "bars" / "bars_MNQ_test_2025_2026.csv"
BARS_TRAIN = _REPO_ROOT / "bars" / "bars_MNQ_train_2024.csv"
EQUITY_DIR = _REPO_ROOT / "research" / "equity_b47"
EQUITY_EXPORT = _REPO_ROOT / "scripts" / "equity_export.py"
PYTHON = sys.executable
HAIRCUT = Decimal("200")
STARTING = Decimal("50000")
TARGET_EQ = Decimal("53000")
MLL_OFFSET = Decimal("2000")

DEPLOYED_FLAGS = [
    "--set", "stop_buffer=3.0",
    "--set", "min_absolute_body=5.0",
    "--set", "r_multiple=3.5",
    "--set", "orb_r_multiple=2.5",
    "--set", "ifvg_entry_mode=close",
    "--set", "swing_stop_lookback=30",
]

CONFIGS = [
    {
        "name": "baseline",
        "label": "Combined baseline (no gate)",
        "gate": False,
    },
    {
        "name": "gate",
        "label": "Combined + confluence gate (G1+G2)",
        "gate": True,
    },
]


# ── Funded pipeline ──────────────────────────────────────────────────────────


def run_equity_export(year: str, cfg_name: str, gate: bool) -> Path:
    out = EQUITY_DIR / f"b47_{cfg_name}_{year}.csv"
    if out.exists():
        print(f"  skip {out.name} (exists)", flush=True)
        return out
    bars = BARS_YEARLY / f"bars_MNQ_dbv_{year}.csv"
    if not bars.exists():
        print(f"  SKIP: {bars.name} not found", flush=True)
        return out
    extra = ["--set", "ifvg_orb_confluence_gate=True"] if gate else []
    cmd = [
        PYTHON, str(EQUITY_EXPORT),
        "--bars", str(bars),
        "--instrument", "MNQ",
        "--timeframe", "5min",
        "--risk-pct", "1.0",
        "--out", str(out),
    ] + DEPLOYED_FLAGS + extra
    print(f"  running {out.name}...", end=" ", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
    if r.returncode != 0:
        print(f"ERROR\n{r.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)
    last_line = r.stdout.strip().split("\n")[-1]
    print(last_line, flush=True)
    return out


def stitch(cfg_name: str) -> list[tuple[datetime, Decimal]]:
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in YEARS:
        p = EQUITY_DIR / f"b47_{cfg_name}_{year}.csv"
        if not p.exists():
            continue
        seg: list[tuple[datetime, Decimal]] = []
        with p.open(newline="") as f:
            for row in csv.DictReader(f):
                seg.append((datetime.fromisoformat(row["ts"]), Decimal(row["equity"])))
        if not seg:
            continue
        if curve:
            offset = curve[-1][1] - STARTING
        curve.extend((ts, eq + offset) for ts, eq in seg)
    return curve


def funded_stats(curve: list[tuple[datetime, Decimal]]) -> dict:
    if not curve:
        return {}
    daily = daily_pnls_from_equity(curve)
    c = simulate_combines(daily, haircut=HAIRCUT)
    x = simulate_xfa_chain(daily, haircut=HAIRCUT)
    sust = (
        Decimal(str(c["passes"])) / Decimal(str(x["busts"]))
        if x["busts"] else Decimal("inf")
    )
    return {
        "c_passes": c["passes"], "c_attempts": c["attempts"],
        "x_accounts": x["accounts"], "x_busts": x["busts"],
        "x_net": float(x["net_payouts"]),
        "sust": float(sust),
    }


# ── Monthly combine ──────────────────────────────────────────────────────────


def eval_month_equity(equity_curve: list[tuple[datetime, Decimal]]) -> bool:
    """Same trailing-MLL + consistency gate logic as run_monthly_combine.py."""
    hwm = STARTING
    best_day = Decimal("0")
    day_pnl = Decimal("0")
    prev_eq = STARTING
    cur_day = None
    for ts, eq in equity_curve:
        td = _trading_day_ct(ts)
        if cur_day is None:
            cur_day = td
        elif td != cur_day:
            cur_day = td
            day_pnl = Decimal("0")
        day_pnl += eq - prev_eq
        prev_eq = eq
        if day_pnl > best_day:
            best_day = day_pnl
        if eq > hwm:
            hwm = eq
        floor = min(hwm - MLL_OFFSET, STARTING)
        if eq <= floor:
            return False
        profit = eq - STARTING
        if eq >= TARGET_EQ and best_day < profit / 2:
            return True
    return False


async def combine_pass_rate(bars_path: Path, strategy, label: str) -> dict:
    bars_all = load_bars_csv(str(bars_path), "MNQ", "5min")
    months: dict[str, list] = {}
    for b in bars_all:
        m = f"{b.ts.year}-{b.ts.month:02d}"
        months.setdefault(m, []).append(b)

    passes = 0
    total = 0
    for month_label in sorted(months):
        mbars = months[month_label]
        if not mbars:
            continue
        cfg = BacktestConfig(
            instrument="MNQ",
            bars=mbars,
            starting_balance=STARTING,
            timeframe="5min",
            contracts=2,
            risk_per_trade_pct=Decimal("1.25"),
            partial_profit_r=Decimal("1.5"),
            enabled_killzones=["all"],
            strategy_params=strategy,
            enforce_risk_limits=True,
        )
        result = await run_backtest(cfg)
        passed = eval_month_equity(result.stats.equity_curve)
        if passed:
            passes += 1
        total += 1
    return {
        "label": label,
        "passes": passes,
        "total": total,
        "pass_rate": passes / total if total else 0.0,
    }


def build_strategy(bot_cfg, gate: bool):
    # strategy_for already applies strategy_overrides.MNQ (stop_buffer=3.0 etc.)
    strategy = strategy_for(bot_cfg, "MNQ")
    if gate:
        strategy = strategy.model_copy(update={"ifvg_orb_confluence_gate": True})
    return strategy


def main() -> None:
    EQUITY_DIR.mkdir(parents=True, exist_ok=True)
    bot_cfg = load_bot_config(_REPO_ROOT / "bot_config.json")

    # ── Part 1: Funded pipeline ────────────────────────────────────────────
    print("\n=== B47 FUNDED PIPELINE (5y excl 2022, risk=1.0%, haircut=$200) ===")
    funded_results = {}
    for cfg in CONFIGS:
        print(f"\n{cfg['label']}")
        for year in YEARS:
            run_equity_export(year, cfg["name"], cfg["gate"])
        curve = stitch(cfg["name"])
        s = funded_stats(curve)
        funded_results[cfg["name"]] = {**s, "label": cfg["label"]}
        print(f"  combine: {s.get('c_passes',0)}/{s.get('c_attempts',0)} passes | "
              f"xfa: {s.get('x_accounts',0)} accts {s.get('x_busts',0)} busts "
              f"net=${s.get('x_net',0):.0f} | sust={s.get('sust',0):.2f}x")

    # ── Part 2: Monthly combine benchmark ─────────────────────────────────
    print("\n=== B47 COMBINE PASS RATE (risk=1.25%, 2 contracts) ===")
    combine_results = {}
    for bars_label, bars_path in [("test", BARS_TEST), ("train", BARS_TRAIN)]:
        if not bars_path.exists():
            print(f"  SKIP {bars_label}: file not found")
            continue
        for cfg in CONFIGS:
            strategy = build_strategy(bot_cfg, cfg["gate"])
            key = f"{cfg['name']}_{bars_label}"
            print(f"  {cfg['label']} [{bars_label}]...", flush=True)
            r = asyncio.run(combine_pass_rate(bars_path, strategy,
                                              f"{cfg['label']} [{bars_label}]"))
            combine_results[key] = r
            print(f"    -> {r['passes']}/{r['total']} passes ({r['pass_rate']*100:.1f}%)")

    # ── Funded summary table ───────────────────────────────────────────────
    print("\n=== FUNDED SUMMARY ===")
    print(f"{'Config':<42} {'C_pass':>6} {'C_atm':>6} {'Accts':>5} {'Busts':>5} "
          f"{'XFA_net$':>8} {'Sust':>6}")
    print("-" * 82)
    for name, s in funded_results.items():
        print(f"  {s['label']:<40} {s.get('c_passes',0):6d} {s.get('c_attempts',0):6d} "
              f"{s.get('x_accounts',0):5d} {s.get('x_busts',0):5d} "
              f"${s.get('x_net',0):>7.0f} {s.get('sust',0):>5.2f}x")
    print(f"  B42 Phase A+B reference:               ---    ---   ---   ---  "
          f"       ---   3.23x")

    # ── Combine summary ────────────────────────────────────────────────────
    if combine_results:
        print("\n=== COMBINE SUMMARY ===")
        for key, r in combine_results.items():
            print(f"  {r['label']:<55} {r['passes']:>2}/{r['total']:>2} "
                  f"({r['pass_rate']*100:.1f}%)")

    # ── Success criteria ─────────────────────────────────────────────────
    print("\n=== B47 SUCCESS CRITERIA ===")
    base_f = funded_results.get("baseline", {})
    gate_f = funded_results.get("gate", {})
    base_c = combine_results.get("baseline_test", {})
    gate_c = combine_results.get("gate_test", {})

    sust_ok = None
    pass_ok = None
    if base_f and gate_f:
        sust_ok = gate_f.get("sust", 0) > base_f.get("sust", 0)
        delta_sust = gate_f.get("sust", 0) - base_f.get("sust", 0)
        print(f"  Primary (gate sust > baseline sust): "
              f"{gate_f.get('sust',0):.2f}x vs {base_f.get('sust',0):.2f}x "
              f"(delta {delta_sust:+.2f}x) -> {'PASS' if sust_ok else 'FAIL'}")

    if base_c and gate_c:
        pass_ok = gate_c.get("passes", 0) >= base_c.get("passes", 0)
        print(f"  Secondary (gate passes >= baseline passes [test]): "
              f"{gate_c.get('passes',0)}/{gate_c.get('total',0)} vs "
              f"{base_c.get('passes',0)}/{base_c.get('total',0)} "
              f"-> {'PASS' if pass_ok else 'FAIL'}")

    if sust_ok is not None and pass_ok is not None:
        both_worse = sust_ok is False and pass_ok is False
        print(f"\n  Stop rule: {'TRIGGERED -> REJECT' if both_worse else 'not triggered'}")
        if both_worse:
            verdict = "REJECTED (stop rule — both sust and pass rate worse)"
        elif sust_ok:
            verdict = "CANDIDATE (primary passed)"
        else:
            verdict = "REJECTED (primary failed — sust did not improve)"
        print(f"  Verdict: {verdict}")


if __name__ == "__main__":
    main()
