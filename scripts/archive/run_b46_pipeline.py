"""
B46 pipeline: B42+B43 deployed-config integration benchmark.

Tests whether orb_signal_window_mins=60 (B43 candidate) improves pipeline economics
when the DEPLOYED Phase B config is used (partial_r=1.5, stop_buffer=3.0,
min_absolute_body=5.0) instead of the research-parity baseline (partial_r=0).

Phase A: equity_b42/deployed_r1p0_{year}.csv  (42 passes, $568 reset, $549/mo B42 result)
Phase B w=0:  equity_b46/orb_reentry_w0_r0p75_{year}.csv  (deployed settings, no cutoff)
Phase B w=60: equity_b46/orb_reentry_w60_r0p75_{year}.csv (deployed settings + 60min cutoff)

Reference Phase B (from B21, partial_r=0): equity_b21/orb_reentry_r0p75_{year}.csv
  B42 used this reference Phase B and reported $549/mo, sust 3.23x

Success criteria:
  Primary:   w=60 Phase B sust > w=0 Phase B sust
  Secondary: B42+deployed-Phase-B beats B42 ($549/mo, 3.23x) on BOTH metrics
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

YEARS = ["2021", "2023", "2024", "2025", "2026"]
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B21_BENCHMARK = {"net_per_month": 497.0, "sustainability": 2.62}
B31_BENCHMARK = {"net_per_month": 508.0, "sustainability": 2.85}
B42_BENCHMARK = {"net_per_month": 549.0, "sustainability": 3.23}


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
        seg_start = seg[0][1]
        for ts, eq in seg:
            curve.append((ts, eq - seg_start + offset))
        offset = curve[-1][1]
    return curve


def phase_stats(equity_dir: Path, prefix: str, label: str) -> dict:
    curve = stitch(equity_dir, prefix)
    if not curve:
        return {}
    daily = daily_pnls_from_equity(curve)
    trading_days = len(daily)
    c = simulate_combines(daily, haircut=HAIRCUT)
    x = simulate_xfa_chain(daily, haircut=HAIRCUT)
    avg_days_per_attempt = (
        Decimal(str(trading_days)) / Decimal(str(c["attempts"]))
        if c["attempts"] else Decimal("0")
    )
    attempts_per_funded = (
        Decimal(str(c["attempts"])) / Decimal(str(c["passes"]))
        if c["passes"] else Decimal("inf")
    )
    combine_days_per_funded = attempts_per_funded * avg_days_per_attempt
    reset_fee_per_funded = attempts_per_funded * COMBINE_RESET_FEE
    avg_funded_days = (
        Decimal(str(trading_days)) / Decimal(str(x["accounts"]))
        if x["accounts"] else Decimal("0")
    )
    net_per_funded = (
        Decimal(str(x["net_payouts"])) / Decimal(str(x["accounts"]))
        if x["accounts"] else Decimal("0")
    )
    sustainability = (
        Decimal(str(c["passes"])) / Decimal(str(x["busts"]))
        if x["busts"] else Decimal("inf")
    )
    return {
        "label": label,
        "trading_days": trading_days,
        "c_passes": c["passes"], "c_attempts": c["attempts"],
        "c_days_per_funded": float(combine_days_per_funded),
        "c_reset_fee": float(reset_fee_per_funded),
        "x_accounts": x["accounts"], "x_busts": x["busts"],
        "x_net_per_account": float(net_per_funded),
        "x_avg_days": float(avg_funded_days),
        "sustainability": float(sustainability),
        "x_net_payouts": float(x["net_payouts"]),
    }


def pipeline_economics(a: dict, b: dict) -> dict:
    reset_cost = Decimal(str(a["c_reset_fee"]))
    xfa_net = Decimal(str(b["x_net_per_account"]))
    net_per_cycle = xfa_net - reset_cost
    combine_days = Decimal(str(a["c_days_per_funded"]))
    funded_days = Decimal(str(b["x_avg_days"]))
    cycle_days = combine_days + funded_days
    net_per_month = (net_per_cycle * TRADING_DAYS_PER_MONTH / cycle_days
                     if cycle_days else Decimal("0"))
    sustainability = (
        Decimal(str(a["c_passes"])) / Decimal(str(b["x_busts"]))
        if b.get("x_busts") else Decimal("inf")
    )
    return {
        "reset_cost": float(reset_cost), "xfa_net": float(xfa_net),
        "net_per_cycle": float(net_per_cycle), "cycle_days": float(cycle_days),
        "net_per_month": float(net_per_month), "sustainability": float(sustainability),
    }


def main() -> None:
    eq_b21 = _REPO_ROOT / "research" / "equity_b21"
    eq_b42 = _REPO_ROOT / "research" / "equity_b42"
    eq_b46 = _REPO_ROOT / "research" / "equity_b46"

    # ── Phase A ─────────────────────────────────────────────────────────────
    print("\n=== PHASE A (B42 deployed r=1.0%, 5y excl 2022) ===")
    s_a = phase_stats(eq_b42, "deployed_r1p0", "B42 deployed Phase A")
    if not s_a:
        print("ERROR: missing B42 Phase A equity", file=sys.stderr)
        sys.exit(1)
    print(f"  {s_a['label']}: {s_a['c_passes']} passes / {s_a['c_attempts']} attempts | "
          f"{s_a['c_days_per_funded']:.1f}d/funded | ${s_a['c_reset_fee']:.0f} reset/funded")

    # ── Phase B standalone ───────────────────────────────────────────────────
    print("\n=== PHASE B STANDALONE (5y excl 2022, haircut $200) ===")
    print(f"{'Config':<38} {'Accts':>5} {'Busts':>5} {'$/acct':>7} {'d/acct':>6} {'sust':>6}")
    print("-" * 68)

    configs_b = [
        (eq_b21, "orb_reentry_r0p75",    "B21 ref (partial_r=0)"),
        (eq_b46, "orb_reentry_w0_r0p75", "B46 deployed w=0 (baseline)"),
        (eq_b46, "orb_reentry_w60_r0p75","B46 deployed w=60 (test)"),
    ]
    stats_b: dict[str, dict] = {}
    for eq_dir, prefix, label in configs_b:
        s = phase_stats(eq_dir, prefix, label)
        if not s:
            print(f"  {label}: MISSING")
            continue
        stats_b[label] = s
        print(f"  {label:<36} {s['x_accounts']:5d} {s['x_busts']:5d} "
              f"${s['x_net_per_account']:>6.0f} {s['x_avg_days']:>6.1f}d {s['sustainability']:>5.2f}x")

    # ── Two-phase pipeline ───────────────────────────────────────────────────
    print("\n=== TWO-PHASE PIPELINE: B42 Phase A -> Phase B variants ===")
    print(f"{'Phase B':<38} {'Reset$':>7} {'XFA$/ac':>7} {'$/cyc':>7} {'cyc d':>6} {'$/mo':>6} {'sust':>6}")
    print("-" * 80)

    results = []
    for label, s_b in stats_b.items():
        if not s_b:
            continue
        econ = pipeline_economics(s_a, s_b)
        results.append((label, econ))
        beats = ""
        if (econ["net_per_month"] >= B42_BENCHMARK["net_per_month"]
                and econ["sustainability"] >= B42_BENCHMARK["sustainability"]):
            beats = " *** BEATS B42"
        elif (econ["net_per_month"] >= B31_BENCHMARK["net_per_month"]
              and econ["sustainability"] >= B31_BENCHMARK["sustainability"]):
            beats = " ** BEATS B31"
        elif (econ["net_per_month"] >= B21_BENCHMARK["net_per_month"]
              and econ["sustainability"] >= B21_BENCHMARK["sustainability"]):
            beats = " * BEATS B21"
        print(f"  {label:<36} ${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>5.0f}  "
              f"${econ['net_per_cycle']:>5.0f}  {econ['cycle_days']:>5.1f}d  "
              f"${econ['net_per_month']:>5.0f}  {econ['sustainability']:>5.2f}x{beats}")

    # ── B46 success criteria ─────────────────────────────────────────────────
    print("\n=== B46 SUCCESS CRITERIA ===")
    b46_w0  = stats_b.get("B46 deployed w=0 (baseline)")
    b46_w60 = stats_b.get("B46 deployed w=60 (test)")
    if b46_w0 and b46_w60:
        econ_w0  = pipeline_economics(s_a, b46_w0)
        econ_w60 = pipeline_economics(s_a, b46_w60)
        primary_ok = econ_w60["sustainability"] > econ_w0["sustainability"]
        print(f"  Primary (w=60 sust > w=0 sust): "
              f"{econ_w60['sustainability']:.2f}x vs {econ_w0['sustainability']:.2f}x "
              f"-> {'PASS' if primary_ok else 'FAIL'}")
        secondary_ok = (econ_w60["net_per_month"] >= B42_BENCHMARK["net_per_month"]
                        and econ_w60["sustainability"] >= B42_BENCHMARK["sustainability"])
        print(f"  Secondary (w=60 beats B42 $549/mo, 3.23x): "
              f"${econ_w60['net_per_month']:.0f}/mo, {econ_w60['sustainability']:.2f}x "
              f"-> {'PASS' if secondary_ok else 'FAIL'}")
        # Phase B bust change
        bust_delta = b46_w60["x_busts"] - b46_w0["x_busts"]
        bust_pct   = bust_delta / b46_w0["x_busts"] * 100 if b46_w0["x_busts"] else 0
        print(f"  Phase B bust count: w=0={b46_w0['x_busts']} -> w=60={b46_w60['x_busts']} "
              f"({bust_delta:+d}, {bust_pct:+.1f}%)")
        acct_delta = b46_w60["x_net_per_account"] - b46_w0["x_net_per_account"]
        print(f"  Phase B $/acct:     w=0=${b46_w0['x_net_per_account']:.0f} -> "
              f"w=60=${b46_w60['x_net_per_account']:.0f} ({acct_delta:+.0f})")

    print()
    print("Benchmarks: B21=$497/mo 2.62x | B31=$508/mo 2.85x | B42=$549/mo 3.23x")
    print("Note: B42 published result used B21 Phase B (partial_r=0).")
    print("B46 baseline (w=0) is the SAME deployed Phase B but with partial_r=1.5.")
    print()
    print("Deployment question: does enabling orb_signal_window_mins=60 on live bot")
    print("improve pipeline sustainability with the full deployed config?")


if __name__ == "__main__":
    main()
