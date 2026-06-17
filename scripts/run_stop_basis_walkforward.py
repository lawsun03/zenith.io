"""Stop-basis walk-forward: does the in-sample "default beats sweep_extreme"
finding hold out-of-sample? (follow-up to scripts/run_stop_basis_sweep.py)

Runs the deployed MNQ config (strategy_for overrides applied), iFVG leg isolated,
for stop_basis ∈ {default, sweep_extreme} on the established train(2024) /
test(2025-26) split CSVs. Risk limits OFF to isolate the stop effect (matches
the in-sample sweep). Verdict = does `default` win in BOTH periods.

Run: .venv/Scripts/python.exe scripts/run_stop_basis_walkforward.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root on path

from app.bot_config import load_bot_config, strategy_for
from app.backtest.runner import BacktestConfig, run_backtest
from app.replay import load_bars_csv

INSTRUMENT = "MNQ"
TIMEFRAME = "5min"
PERIODS = {
    "train_2024": "bars/bars_MNQ_train_2024.csv",
    "test_2025_2026": "bars/bars_MNQ_test_2025_2026.csv",
}
BASES = ["default", "sweep_extreme"]


async def _run(sp, bars_path, basis, contracts, partial_r) -> dict:
    cfg = BacktestConfig(
        instrument=INSTRUMENT,
        bars=load_bars_csv(bars_path, instrument=INSTRUMENT, timeframe=TIMEFRAME),
        strategy_params=sp.model_copy(update={"stop_basis": basis}),
        timeframe=TIMEFRAME,
        contracts=contracts,
        partial_profit_r=partial_r,
        enforce_risk_limits=False,
    )
    r = await run_backtest(cfg)
    s = r.stats
    return {
        "trades": s.trades, "win_rate": s.win_rate,
        "profit_factor": s.profit_factor, "net_pnl": float(s.net_pnl),
        "max_drawdown": float(s.max_drawdown), "expectancy": float(s.expectancy),
    }


async def main() -> None:
    bot = load_bot_config(Path("bot_config.json"))
    sp = strategy_for(bot, INSTRUMENT).model_copy(update={"engine": "ifvg"})
    contracts = getattr(bot, "contracts", 2)
    partial_r = Decimal(str(getattr(bot, "partial_profit_r", "1.5")))
    print(f"Stop-basis WALK-FORWARD | {INSTRUMENT} {TIMEFRAME} iFVG | deployed MNQ override")
    print(f"stop_buffer={sp.stop_buffer} body={sp.body_atr_multiple} r={sp.r_multiple} "
          f"swing_lookback={sp.swing_stop_lookback} | risk limits OFF\n")

    out: dict = {}
    for period, path in PERIODS.items():
        out[period] = {}
        for basis in BASES:
            out[period][basis] = await _run(sp, path, basis, contracts, partial_r)

    hdr = f"{'period':<16}{'basis':<16}{'trades':>8}{'win%':>7}{'PF':>7}{'net_pnl':>12}{'max_dd':>11}"
    print(hdr); print("-" * len(hdr))
    for period in PERIODS:
        for basis in BASES:
            x = out[period][basis]
            pf = f"{x['profit_factor']:.2f}" if x["profit_factor"] is not None else "n/a"
            print(f"{period:<16}{basis:<16}{x['trades']:>8}{x['win_rate']:>7.1f}"
                  f"{pf:>7}{x['net_pnl']:>12,.0f}{x['max_drawdown']:>11,.0f}")

    # Verdict
    def better(period):
        d, s = out[period]["default"], out[period]["sweep_extreme"]
        return "default" if d["net_pnl"] > s["net_pnl"] else "sweep_extreme"
    tr, te = better("train_2024"), better("test_2025_2026")
    print(f"\nwinner train_2024={tr} | test_2025_2026={te}")
    print("VERDICT:", "default robust (wins both)" if tr == te == "default"
          else "sweep_extreme robust (wins both)" if tr == te == "sweep_extreme"
          else "REGIME-DEPENDENT (ranking flips) — do not change live on this")

    outf = Path("research") / "stop_basis_walkforward_2026-06-16.json"
    outf.parent.mkdir(parents=True, exist_ok=True)
    outf.write_text(json.dumps(out, indent=2))
    print(f"saved: {outf}")


if __name__ == "__main__":
    asyncio.run(main())
