"""B106 benchmark: iFVGxORB alignment up-only sizing vs matched-risk control.

Phase 1: gate verification (aligned PF/WR/r_mfe from mfe_mae_deployed_combined_clean.csv).
Phase 2: year-by-year robustness.
Phase 3: f-sweep benchmark (B106 PF vs matched-risk control PF).
Phase 4: funded_sim using Phase B equity_b21 CSVs with per-day alignment scaling.
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.backtest.funded_sim import (
    daily_pnls_from_equity,
    simulate_xfa_chain,
)
from app.risk.account_phase import CombineRules, XfaRules

CLEAN_CSV = ROOT / "research" / "mfe_mae_deployed_combined_clean.csv"
EQUITY_B21_DIR = ROOT / "research" / "equity_b21"
YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")

# â”€â”€â”€ helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def pf_wr_rmfe(trades: list[dict]) -> tuple[float, float, float, int]:
    gains = sum(float(r["pnl_usd"]) for r in trades if float(r["pnl_usd"]) > 0)
    losses = sum(-float(r["pnl_usd"]) for r in trades if float(r["pnl_usd"]) < 0)
    wins = sum(1 for r in trades if float(r["pnl_usd"]) > 0)
    n = len(trades)
    pf = gains / losses if losses else float("inf")
    wr = wins / n if n else 0.0
    rmfe = sum(float(r["r_mfe"]) for r in trades) / n if n else 0.0
    return pf, wr, rmfe, n


def stitch_b21(year_list: list[str] = YEARS, prefix: str = "orb_reentry_r0p75") -> list[tuple[datetime, Decimal]]:
    """Stitch per-year Phase B equity CSVs into a continuous curve."""
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in year_list:
        p = EQUITY_B21_DIR / f"{prefix}_{year}.csv"
        if not p.exists():
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


def scale_daily_pnls(
    daily: list[tuple[datetime, Decimal]],
    aligned_dates: set[str],
    f: float,
    uniform: bool = False,
) -> list[tuple[datetime, Decimal]]:
    """Scale daily P&Ls.

    If uniform=True: scale every day by f (matched-risk control).
    If uniform=False: scale aligned dates by f, others by 1.0 (B106 variant).
    """
    F = Decimal(str(f))
    result = []
    for ts, pnl in daily:
        date_str = ts.date().isoformat()
        if uniform:
            result.append((ts, pnl * F))
        elif date_str in aligned_dates:
            result.append((ts, pnl * F))
        else:
            result.append((ts, pnl))
    return result


def run_funded_sim(
    daily: list[tuple[datetime, Decimal]],
    haircut: Decimal = Decimal("200"),
    label: str = "",
) -> dict:
    x = simulate_xfa_chain(daily, haircut=haircut)
    busts = x["busts"]
    accts = x["accounts"]
    net = float(x["net_payouts"])
    mo = net / 61  # 61 months in 5y excl 2022
    # combine_passes comes from phase_stats in run_b42; here we use fixed B21 reference
    return dict(label=label, net=net, busts=busts, accts=accts, mo=mo)


# â”€â”€â”€ Phase 1: load and classify â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

with open(CLEAN_CSV) as f:
    all_rows = list(csv.DictReader(f))

ifvg_rows = [r for r in all_rows if r["engine_type"] == "ifvg"]
orb_rows  = [r for r in all_rows if r["engine_type"] == "orb"]

ifvg_by_date: dict[str, list[tuple[str, str]]] = defaultdict(list)
for r in ifvg_rows:
    ifvg_by_date[r["entry_ts"][:10]].append((r["side"], r["entry_ts"]))

aligned_orb:  list[dict] = []
opposed_orb:  list[dict] = []
neutral_orb:  list[dict] = []
aligned_dates: set[str] = set()

for r in orb_rows:
    date      = r["entry_ts"][:10]
    orb_ts    = r["entry_ts"]
    orb_side  = r["side"]
    prior_ifvg = [(s, ts) for s, ts in ifvg_by_date[date] if ts < orb_ts]
    if not prior_ifvg:
        neutral_orb.append(r)
    else:
        same_dir = [s for s, ts in prior_ifvg if s == orb_side]
        if same_dir:
            aligned_orb.append(r)
            aligned_dates.add(date)
        else:
            opposed_orb.append(r)

unaligned_orb = opposed_orb + neutral_orb
p_aligned = len(aligned_orb) / len(orb_rows)

print("=" * 62)
print("PHASE 1 â€” Gate verification")
print("=" * 62)
for label, subset in [
    ("aligned",    aligned_orb),
    ("opposed",    opposed_orb),
    ("neutral",    neutral_orb),
    ("all_orb",    orb_rows),
]:
    pf, wr, rmfe, n = pf_wr_rmfe(subset)
    print(f"  {label:10s}: n={n:3d}  PF={pf:.3f}  WR={wr:.1%}  r_mfe={rmfe:.3f}")

gate_pf, gate_wr, _, _ = pf_wr_rmfe(aligned_orb)
gate_ok = abs(gate_pf - 1.730) < 0.05 and abs(gate_wr - 0.486) < 0.02
print(f"\n  p_aligned = {p_aligned:.1%}  ({len(aligned_orb)}/{len(orb_rows)} ORB trades)")
print(f"  Gate: {'PASS' if gate_ok else 'FAIL'}  (spec: aligned PF 1.73, WR 48.6%)")

if not gate_ok:
    print("  Gate FAIL â€” stopping.", file=sys.stderr)
    sys.exit(1)

# â”€â”€â”€ Phase 2: year-by-year robustness â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

print("\n" + "=" * 62)
print("PHASE 2 â€” Year-by-year (aligned PF vs all_orb PF)")
print("=" * 62)
print(f"  {'Year':<6} {'Aligned PF':>11} {'Control PF':>11} {'n_aligned':>10} {'Beat?':>6}")
years_present = sorted({r["entry_ts"][:4] for r in orb_rows} - {"2022"})
beats = 0
for yr in years_present:
    a = [r for r in aligned_orb  if r["entry_ts"][:4] == yr]
    c = [r for r in orb_rows     if r["entry_ts"][:4] == yr]
    pf_a = pf_wr_rmfe(a)[0] if a else 0.0
    pf_c = pf_wr_rmfe(c)[0] if c else 0.0
    beat = pf_a > pf_c
    if beat:
        beats += 1
    print(f"  {yr:<6} {pf_a:>11.3f} {pf_c:>11.3f} {len(a):>10}  {'YES' if beat else 'no':>6}")
print(f"\n  Aligned beat control in {beats}/{len(years_present)} years")

# â”€â”€â”€ Phase 3: f-sweep PF benchmark â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

print("\n" + "=" * 62)
print("PHASE 3 â€” Matched-risk f-sweep (B106 PF vs control)")
print("=" * 62)

aligned_gains    = sum(float(r["pnl_usd"]) for r in aligned_orb   if float(r["pnl_usd"]) > 0)
aligned_losses   = sum(-float(r["pnl_usd"]) for r in aligned_orb  if float(r["pnl_usd"]) < 0)
unaligned_gains  = sum(float(r["pnl_usd"]) for r in unaligned_orb if float(r["pnl_usd"]) > 0)
unaligned_losses = sum(-float(r["pnl_usd"]) for r in unaligned_orb if float(r["pnl_usd"]) < 0)
control_pf, control_wr, control_rmfe, _ = pf_wr_rmfe(orb_rows)

print(f"  {'f':>5} {'avg_mult':>9} {'B106_PF':>9} {'ctrl_PF':>9} {'Î”PNL_rmfe':>10} {'Î”_PF':>8}")
fs = [1.25, 1.5, 2.0]
f_results: dict[float, dict] = {}
for f in fs:
    avg = p_aligned * f + (1 - p_aligned) * 1.0
    b106_gains  = f * aligned_gains  + unaligned_gains
    b106_losses = f * aligned_losses + unaligned_losses
    b106_pf     = b106_gains / b106_losses
    aligned_rmfe_val  = pf_wr_rmfe(aligned_orb)[2]
    unaligned_rmfe_val = pf_wr_rmfe(unaligned_orb)[2]
    b106_rmfe_dw = (
        f * len(aligned_orb) * aligned_rmfe_val
        + len(unaligned_orb) * unaligned_rmfe_val
    ) / (f * len(aligned_orb) + len(unaligned_orb))
    delta_pf = b106_pf - control_pf
    print(
        f"  {f:>5.2f} {avg:>9.3f} {b106_pf:>9.3f} {control_pf:>9.3f} "
        f"{b106_rmfe_dw:>10.3f} {delta_pf:>+8.3f}"
    )
    f_results[f] = dict(avg=avg, b106_pf=b106_pf, delta_pf=delta_pf)

# â”€â”€â”€ Phase 4: funded_sim â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

print("\n" + "=" * 62)
print("PHASE 4 â€” funded_sim (Phase B equity_b21 + alignment scaling)")
print("=" * 62)

base_curve  = stitch_b21()
if not base_curve:
    print("  ERROR: no Phase B equity CSVs found", file=sys.stderr)
    sys.exit(1)

base_daily = daily_pnls_from_equity(base_curve)

# Check how many Phase B trade days overlap with our alignment_dates
b21_trade_dates = {ts.date().isoformat() for ts, pnl in base_daily if pnl != 0}
overlap = len(aligned_dates & b21_trade_dates)
total_b21_trade_days = len([x for x in base_daily if x[1] != 0])
print(f"\n  Phase B trade days: {total_b21_trade_days}")
print(f"  Aligned dates from combined CSV: {len(aligned_dates)}")
print(f"  Phase B days that are aligned: {overlap} / {total_b21_trade_days} ({overlap/total_b21_trade_days:.1%})")

print(f"\n  {'Config':<22} {'h=0 net':>10} {'h=0 bust':>9} {'h=200 net':>10} {'h=200 bust':>11} {'h=200 $/mo':>11}")

configs: list[tuple[str, list, bool, float]] = [
    # (label, daily_pnl_series, is_computed?, f)
]

# baseline (no scaling = f=1.0 for all)
for f in [1.0, 1.25, 1.5]:
    if f == 1.0:
        daily = base_daily
        lbl = "baseline (f=1.0)"
    else:
        # B106: scale aligned days by f, others by 1.0
        daily = scale_daily_pnls(base_daily, aligned_dates, f, uniform=False)
        lbl = f"B106 f={f:.2f}"
    configs.append((lbl, daily))

# matched-risk controls for f=1.25 and f=1.5
for f in [1.25, 1.5]:
    avg = p_aligned * f + (1 - p_aligned) * 1.0
    daily_ctrl = scale_daily_pnls(base_daily, aligned_dates, avg, uniform=True)
    lbl = f"ctrl avg={avg:.3f}"
    configs.append((lbl, daily_ctrl))

for lbl, daily in configs:
    r0  = run_funded_sim(daily, haircut=Decimal("0"),   label=lbl)
    r200 = run_funded_sim(daily, haircut=Decimal("200"), label=lbl)
    print(
        f"  {lbl:<22}  {r0['net']:>10,.0f} {r0['busts']:>9}  "
        f"{r200['net']:>10,.0f} {r200['busts']:>11}  {r200['mo']:>11,.0f}"
    )

print()
print("  Note: Phase B ORB-reentry includes reentry trades not in combined CSV;")
print("        reentry days classified as 'neutral' (1.0x) â€” small upward bias for B106.")

