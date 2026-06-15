"""
B50 dynamic risk sizing — pipeline benchmark.

Applies combine_ramp and funded_survival policies to the B42 baseline
equity data and measures impact on combine pass rate, XFA bust rate,
and two-phase pipeline economics.

Policy invariant: per-trade PF is unchanged (path-only rescaling).
Stop rule: if variant is worse on BOTH $/mo AND sustainability vs
baseline → REJECTED.

Equity sources:
  Phase A (Combine):  research/equity_b42/deployed_r1p0_{year}.csv
  Phase B (XFA):      research/equity_b21/orb_reentry_r0p75_{year}.csv
"""
from __future__ import annotations

import csv
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

EQUITY_DIR_B42 = _REPO_ROOT / "research" / "equity_b42"
EQUITY_DIR_B21 = _REPO_ROOT / "research" / "equity_b21"
YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 frozen holdout

BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

BASE_RISK_COMBINE = Decimal("1.0")   # deployed config risk %
BASE_RISK_FUNDED  = Decimal("0.75")  # XFA nominal risk %


def stitch(equity_dir: Path, prefix: str) -> list[tuple[datetime, Decimal]]:
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in YEARS:
        p = equity_dir / f"{prefix}_{year}.csv"
        if not p.exists():
            print(f"  WARNING: missing {p}", file=sys.stderr)
            continue
        seg: list[tuple[datetime, Decimal]] = []
        with p.open(newline="") as f:
            for row in csv.DictReader(f):
                seg.append((datetime.fromisoformat(row["ts"]), Decimal(row["equity"])))
        if not seg:
            continue
        if curve:
            offset = curve[-1][1] - BASELINE
        curve.extend((ts, eq + offset) for ts, eq in seg)
    return curve


def pipeline_econ(
    c_passes: int,
    c_attempts: int,
    avg_attempt_days: float,
    x_busts: int,
    x_accounts: int,
    x_net_per_account: float,
    x_avg_days: float,
) -> dict:
    attempts_per_funded = (
        Decimal(str(c_attempts)) / Decimal(str(c_passes)) if c_passes else Decimal("inf")
    )
    combine_days_per_funded = attempts_per_funded * Decimal(str(avg_attempt_days))
    reset_fee = attempts_per_funded * COMBINE_RESET_FEE
    cycle_days = combine_days_per_funded + Decimal(str(x_avg_days))
    net_per_cycle = Decimal(str(x_net_per_account)) - reset_fee
    net_per_month = net_per_cycle / cycle_days * TRADING_DAYS_PER_MONTH if cycle_days else Decimal("0")
    sustainability = (
        Decimal(str(c_passes)) / Decimal(str(x_busts)) if x_busts else Decimal("inf")
    )
    return {
        "net_per_month": float(net_per_month),
        "sustainability": float(sustainability),
        "combine_days_per_funded": float(combine_days_per_funded),
        "reset_fee": float(reset_fee),
        "cycle_days": float(cycle_days),
    }


def main() -> None:
    print("Loading equity curves...")
    curve_a = stitch(EQUITY_DIR_B42, "deployed_r1p0")
    curve_b = stitch(EQUITY_DIR_B21, "orb_reentry_r0p75")
    if not curve_a or not curve_b:
        print("ERROR: missing equity data", file=sys.stderr)
        sys.exit(1)

    daily_a = daily_pnls_from_equity(curve_a)
    daily_b = daily_pnls_from_equity(curve_b)
    print(f"  Phase A: {len(daily_a)} trading days | Phase B: {len(daily_b)} trading days")
    print()

    # --------------------------------------------------------------------------
    # Phase A: combine pass/bust under different policies
    # --------------------------------------------------------------------------
    print("=== Phase A: Combine simulation ===")
    configs_a = [
        ("baseline",      dict(haircut=HAIRCUT)),
        ("combine_ramp",  dict(haircut=HAIRCUT, risk_policy="combine_ramp",
                               base_risk_pct=BASE_RISK_COMBINE)),
    ]
    results_a: dict[str, dict] = {}
    for name, kwargs in configs_a:
        r = simulate_combines(daily_a, **kwargs)
        trading_days = len(daily_a)
        avg_attempt_days = trading_days / r["attempts"] if r["attempts"] else 0
        results_a[name] = {**r, "avg_attempt_days": avg_attempt_days}
        print(
            f"  {name:<14}: passes {r['passes']:>3} / attempts {r['attempts']:>3} "
            f"busts {r['busts']:>3}  "
            f"avg {avg_attempt_days:.1f}d/attempt  "
            f"median {r['median_days_to_pass'] or 'n/a'}d to pass"
        )
    print()

    # --------------------------------------------------------------------------
    # Phase B: XFA bust/payout under different policies
    # --------------------------------------------------------------------------
    print("=== Phase B: XFA simulation ===")
    configs_b = [
        ("baseline",          dict(haircut=HAIRCUT)),
        ("funded_survival",   dict(haircut=HAIRCUT, risk_policy="funded_survival",
                                   base_risk_pct=BASE_RISK_FUNDED)),
    ]
    results_b: dict[str, dict] = {}
    for name, kwargs in configs_b:
        r = simulate_xfa_chain(daily_b, **kwargs)
        trading_days = len(daily_b)
        avg_funded_days = trading_days / r["accounts"] if r["accounts"] else 0
        net_per_account = float(r["net_payouts"]) / r["accounts"] if r["accounts"] else 0
        results_b[name] = {**r, "avg_funded_days": avg_funded_days, "net_per_account": net_per_account}
        print(
            f"  {name:<16}: busts {r['busts']:>3} / accounts {r['accounts']:>3}  "
            f"net payouts ${float(r['net_payouts']):>7.0f}  "
            f"(${net_per_account:>5.0f}/acct  {avg_funded_days:.1f}d/acct)"
        )
    print()

    # --------------------------------------------------------------------------
    # Two-phase pipeline matrix
    # --------------------------------------------------------------------------
    print("=" * 100)
    print("B50 TWO-PHASE PIPELINE MATRIX")
    print(f"{'Scenario':<40} {'Reset$':>7} {'Net/cyc':>9} {'Cycle d':>9} {'Net/mo':>8} {'Sust':>8}")
    print("-" * 100)

    scenarios = [
        ("baseline -> baseline (B42 ref)",        "baseline",     "baseline"),
        ("combine_ramp -> baseline",               "combine_ramp", "baseline"),
        ("baseline -> funded_survival",            "baseline",     "funded_survival"),
        ("combine_ramp -> funded_survival (B50)",  "combine_ramp", "funded_survival"),
    ]

    econs: list[tuple[str, dict]] = []
    for label, a_key, b_key in scenarios:
        ra = results_a[a_key]
        rb = results_b[b_key]
        econ = pipeline_econ(
            c_passes=ra["passes"],
            c_attempts=ra["attempts"],
            avg_attempt_days=ra["avg_attempt_days"],
            x_busts=rb["busts"],
            x_accounts=rb["accounts"],
            x_net_per_account=rb["net_per_account"],
            x_avg_days=rb["avg_funded_days"],
        )
        econs.append((label, econ))

    baseline_econ = dict(econs[0][1])
    for label, econ in econs:
        beats_mo   = econ["net_per_month"] > baseline_econ["net_per_month"]
        beats_sust = econ["sustainability"] > baseline_econ["sustainability"]
        if label == scenarios[0][0]:
            flag = " [baseline]"
        elif beats_mo and beats_sust:
            flag = " *** BOTH BETTER"
        elif beats_mo or beats_sust:
            flag = " * ONE BETTER"
        else:
            flag = " -- both worse"
        print(
            f"  {label:<40} ${econ['reset_fee']:>5.0f}  "
            f"${econ['net_per_month']:>7.0f}/mo  "
            f"{econ['cycle_days']:>7.1f}d  "
            f"{econ['sustainability']:>7.2f}x{flag}"
        )

    print()
    print("=" * 100)
    print("VERDICT")
    print("=" * 100)
    b42_ref = baseline_econ
    for label, econ in econs[1:]:  # skip baseline
        beats_mo   = econ["net_per_month"] > b42_ref["net_per_month"]
        beats_sust = econ["sustainability"] > b42_ref["sustainability"]
        if beats_mo and beats_sust:
            verdict = "CANDIDATE - better on BOTH $/mo AND sustainability"
        elif beats_mo:
            verdict = "partial - better $/mo, worse sustainability"
        elif beats_sust:
            verdict = "partial - better sustainability, worse $/mo"
        else:
            verdict = "REJECTED - worse on both metrics"
        print(
            f"  {label}: ${econ['net_per_month']:.0f}/mo  "
            f"sust {econ['sustainability']:.2f}x — {verdict}"
        )

    print()
    print("Definitions:")
    print("  combine_ramp:     1.5% early gain -> 0.75% protect -> 0.5% near MLL")
    print("  funded_survival:  0.75% normal -> 0.4% near MLL (<$750 cushion)")
    print("  Sustainability = Phase_A.combine_passes / Phase_B.xfa_busts")
    print("  Net/mo = (XFA$/acct - reset_fee/acct) * 21 / cycle_days")
    print("  Haircut: $200 (intraday MLL proxy). 2022 excluded (holdout).")
    print()
    print("Note: PF is invariant — policies rescale position size, not signal quality.")


if __name__ == "__main__":
    main()
