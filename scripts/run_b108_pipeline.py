"""
B108 driver: ORB-reentry orb_r_multiple=1.5 two-phase pipeline.

Hypothesis: B99 found r_multiple=1.5 is the standalone funded expectancy optimum
(PF=1.24, +9% vs r2.5, year-stable). B102 bootstrap showed r1.5 has fewer funded
busts (p50=24 vs 33 for plain ORB). ORB-reentry at r_multiple=1.5 has never been
tested in the two-phase pipeline. This closes the B99 candidate gap.

Phase A: equity_b42/deployed_r1p0_{year}.csv  (42 passes, B42 deployed Phase A)
Phase B: equity_b108/orb_reentry_rm1p5_{year}.csv  (ORB-reentry r_mult=1.5, r=0.75%, partial_r=0)
Reference B (r_mult=2.5): equity_b21/orb_reentry_r0p75_{year}.csv  (B42 baseline)

Success criteria: B108 (r_mult=1.5) fewer busts (p50 < 13) while CI-NOT-STRONGLY-WORSE
on net payouts vs B21 r_mult=2.5 reference ($549/mo, 3.23x sust, 13 busts p50).
"""
from __future__ import annotations

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
    bootstrap_pipeline,
    daily_pnls_from_equity,
    simulate_combines,
    simulate_xfa_chain,
)

YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

# B108 Phase B parameters
B108_PARAMS = [
    "--set", "engine=orb",
    "--set", "orb_r_multiple=1.5",
    "--set", "orb_reentry_after_stop=True",
    "--set", "swing_stop_lookback=0",
    "--risk-pct", "0.75",
    "--partial-r", "0",
    "--instrument", "MNQ",
    "--timeframe", "5min",
]

B42_BENCHMARK = {"net_per_month": 549.0, "sustainability": 3.23, "busts_p50": 13}

EQUITY_B42  = _REPO_ROOT / "research" / "equity_b42"
EQUITY_B21  = _REPO_ROOT / "research" / "equity_b21"
EQUITY_B108 = _REPO_ROOT / "research" / "equity_b108"
BARS_YEARLY = _REPO_ROOT / "bars" / "yearly"
EQUITY_EXPORT = _REPO_ROOT / "scripts" / "equity_export.py"
PYTHON = sys.executable

N_BOOTSTRAP = 1000
BLOCK_LEN = 20
SEED = 42


def _generate_equity_csv(year: str, out_path: Path) -> bool:
    bars = BARS_YEARLY / f"bars_MNQ_dbv_{year}.csv"
    if not bars.exists():
        print(f"  SKIP {year}: bars not found at {bars}", file=sys.stderr)
        return False
    cmd = [PYTHON, str(EQUITY_EXPORT), "--bars", str(bars), "--out", str(out_path)] + B108_PARAMS
    print(f"  Generating {out_path.name}...", end=" ", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
    if r.returncode != 0:
        print(f"ERROR\n{r.stderr[-2000:]}", file=sys.stderr)
        return False
    last_line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(no output)"
    print(last_line)
    return True


def _ensure_b108_csvs() -> None:
    EQUITY_B108.mkdir(parents=True, exist_ok=True)
    print("=== Generating B108 Phase B equity CSVs (ORB-reentry r_mult=1.5) ===")
    for year in YEARS:
        out = EQUITY_B108 / f"orb_reentry_rm1p5_{year}.csv"
        if out.exists():
            print(f"  Skipping {out.name} (exists)")
        else:
            _generate_equity_csv(year, out)
    print()


def stitch(equity_dir: Path, prefix: str) -> list[tuple[datetime, Decimal]]:
    """Stitch per-year equity CSVs into a continuous curve (each year restarts at BASELINE)."""
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


def phase_stats(equity_dir: Path, prefix: str, label: str, haircut: Decimal = HAIRCUT) -> dict:
    curve = stitch(equity_dir, prefix)
    if not curve:
        return {}
    daily = daily_pnls_from_equity(curve)
    trading_days = len(daily)
    c = simulate_combines(daily, haircut=haircut)
    x = simulate_xfa_chain(daily, haircut=haircut)
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
        x["net_payouts"] / Decimal(str(x["accounts"]))
        if x["accounts"] else Decimal("0")
    )
    sustainability = (
        Decimal(str(c["passes"])) / Decimal(str(x["busts"]))
        if x["busts"] else Decimal("inf")
    )
    return {
        "label": label, "daily": daily,
        "trading_days": trading_days,
        "c_passes": c["passes"], "c_attempts": c["attempts"],
        "c_days_per_funded": float(combine_days_per_funded),
        "c_reset_fee": float(reset_fee_per_funded),
        "x_accounts": x["accounts"], "x_busts": x["busts"],
        "x_net_payouts": float(x["net_payouts"]),
        "x_net_per_account": float(net_per_funded),
        "x_avg_days": float(avg_funded_days),
        "sustainability": float(sustainability),
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
        if b["x_busts"] else Decimal("inf")
    )
    return {
        "reset_cost": float(reset_cost), "xfa_net": float(xfa_net),
        "net_per_cycle": float(net_per_cycle), "cycle_days": float(cycle_days),
        "net_per_month": float(net_per_month), "sustainability": float(sustainability),
    }


def run_bootstrap(label: str, daily: list, n: int = N_BOOTSTRAP) -> dict:
    print(f"  Bootstrapping {label} ({n} resamples, block={BLOCK_LEN}d)...", flush=True)
    return bootstrap_pipeline(daily, n_resamples=n, block_len=BLOCK_LEN, seed=SEED, haircut=HAIRCUT)


def ci_str(ci: dict, metric: str) -> str:
    p5, p50, p95 = ci[metric][5], ci[metric][50], ci[metric][95]
    return f"p5={p5:.0f} p50={p50:.0f} p95={p95:.0f}"


def cis_overlap(ci_a: dict, ci_b: dict, metric: str) -> bool:
    return not (ci_a[metric][95] < ci_b[metric][5] or ci_b[metric][95] < ci_a[metric][5])


def print_phase_b_table(configs: list[dict]) -> None:
    print(f"\n{'Config':<40} {'Busts':>6} {'$/acct':>8} {'d/acct':>7} {'sust':>6} {'Accts':>6}")
    print("-" * 76)
    for s in configs:
        print(f"  {s['label']:<38} {s['x_busts']:>6} {s['x_net_per_account']:>8,.0f}"
              f" {s['x_avg_days']:>7.1f} {s['sustainability']:>6.2f}x {s['x_accounts']:>6}")


def print_twophase_table(pairs: list[tuple[str, dict]]) -> None:
    print(f"\n{'Config':<40} {'$/mo':>8} {'sust':>6} {'Busts':>6}")
    print("-" * 65)
    for label, e in pairs:
        print(f"  {label:<38} {e['net_per_month']:>8,.0f} {e['sustainability']:>6.2f}x"
              f" {e['sustainability']:>6.2f}x")


def main() -> None:
    _ensure_b108_csvs()

    # ── Phase A (B42 deployed, fixed) ─────────────────────────────────────
    print("=== Phase A: B42 deployed r=1.0% (fixed reference) ===")
    s_a = phase_stats(EQUITY_B42, "deployed_r1p0", "B42 deployed Phase A")
    if not s_a:
        print("ERROR: missing B42 Phase A equity files", file=sys.stderr)
        sys.exit(1)
    print(f"  {s_a['label']}: {s_a['c_passes']} passes / {s_a['c_attempts']} attempts "
          f"| {s_a['c_days_per_funded']:.1f}d/funded | ${s_a['c_reset_fee']:.0f} reset/funded")

    # ── Phase B standalone ─────────────────────────────────────────────────
    print("\n=== Phase B standalone (h=$200, 5y excl 2022) ===")
    s_b108 = phase_stats(EQUITY_B108, "orb_reentry_rm1p5", "B108 ORB-reentry r_mult=1.5 r=0.75%")
    s_b21_ref = phase_stats(EQUITY_B21, "orb_reentry_r0p75", "B21 ref ORB-reentry r_mult=2.5 r=0.75%")

    configs = [s for s in [s_b108, s_b21_ref] if s]
    print_phase_b_table(configs)

    # ── Bootstrap CIs ─────────────────────────────────────────────────────
    print("\n=== Bootstrap CIs (N=1000, block=20d, seed=42, h=$200) ===")
    ci_b108, ci_b21_ref = None, None
    if s_b108:
        ci_b108 = run_bootstrap("B108 r_mult=1.5", s_b108["daily"])
        print(f"  B108 r_mult=1.5  xfa_net:  {ci_str(ci_b108, 'xfa_net')}")
        print(f"  B108 r_mult=1.5  xfa_busts:{ci_str(ci_b108, 'xfa_busts')}")
    if s_b21_ref:
        ci_b21_ref = run_bootstrap("B21 ref r_mult=2.5", s_b21_ref["daily"])
        print(f"  B21 ref r_mult=2.5 xfa_net:  {ci_str(ci_b21_ref, 'xfa_net')}")
        print(f"  B21 ref r_mult=2.5 xfa_busts:{ci_str(ci_b21_ref, 'xfa_busts')}")

    if ci_b108 and ci_b21_ref:
        net_tie = cis_overlap(ci_b108, ci_b21_ref, "xfa_net")
        bust_tie = cis_overlap(ci_b108, ci_b21_ref, "xfa_busts")
        print(f"\n  CI overlap on xfa_net:   {'TIE' if net_tie else 'SEPARATED'}")
        print(f"  CI overlap on xfa_busts: {'TIE' if bust_tie else 'SEPARATED'}")

    # ── Two-phase pipeline economics ───────────────────────────────────────
    print("\n=== Two-phase pipeline (Phase A fixed: B42 42 passes, h=$200) ===")
    pairs = []
    if s_b108 and s_a:
        e_b108 = pipeline_economics(s_a, s_b108)
        pairs.append(("B108 r_mult=1.5 (new)", e_b108))
        print(f"  B108 r_mult=1.5: ${e_b108['net_per_month']:,.0f}/mo  sust={e_b108['sustainability']:.2f}x"
              f"  busts={s_b108['x_busts']}  cycle={e_b108['cycle_days']:.0f}d")
    if s_b21_ref and s_a:
        e_ref = pipeline_economics(s_a, s_b21_ref)
        pairs.append(("B21 ref r_mult=2.5 (baseline)", e_ref))
        print(f"  B21 ref r_mult=2.5: ${e_ref['net_per_month']:,.0f}/mo  sust={e_ref['sustainability']:.2f}x"
              f"  busts={s_b21_ref['x_busts']}  cycle={e_ref['cycle_days']:.0f}d")

    # ── Sensitivity: haircut 0 and 400 ────────────────────────────────────
    print("\n=== Haircut sensitivity ===")
    for h in [Decimal("0"), Decimal("400")]:
        s108h = phase_stats(EQUITY_B108, "orb_reentry_rm1p5", f"B108 h={h}", haircut=h)
        srefh = phase_stats(EQUITY_B21, "orb_reentry_r0p75", f"B21ref h={h}", haircut=h)
        if s108h and s_a:
            e108h = pipeline_economics(s_a, s108h)
            print(f"  B108 r_mult=1.5 h={h:>4}: ${e108h['net_per_month']:,.0f}/mo sust={e108h['sustainability']:.2f}x busts={s108h['x_busts']}")
        if srefh and s_a:
            erefh = pipeline_economics(s_a, srefh)
            print(f"  B21 ref h={h:>4}: ${erefh['net_per_month']:,.0f}/mo sust={erefh['sustainability']:.2f}x busts={srefh['x_busts']}")

    # ── Verdict ────────────────────────────────────────────────────────────
    print("\n=== VERDICT ===")
    print(f"  B42 benchmark: ${B42_BENCHMARK['net_per_month']}/mo, {B42_BENCHMARK['sustainability']}x sust, "
          f"{B42_BENCHMARK['busts_p50']} busts p50")
    if s_b108 and s_a:
        e108 = pipeline_economics(s_a, s_b108)
        fewer_busts = s_b108["x_busts"] < s_b21_ref["x_busts"] if s_b21_ref else False
        ci_not_worse = cis_overlap(ci_b108, ci_b21_ref, "xfa_net") if (ci_b108 and ci_b21_ref) else False
        if fewer_busts and ci_not_worse:
            print(f"  => CANDIDATE: r_mult=1.5 has fewer busts ({s_b108['x_busts']} vs "
                  f"{s_b21_ref['x_busts']}) and CI-tied on net payouts.")
            print(f"     r_mult=1.5 is the new Phase B optimum.")
        elif not fewer_busts and not ci_not_worse:
            print(f"  => REJECTED: B70 finding extends to reentry. r_mult=2.5 confirmed as Phase B optimum.")
        else:
            print(f"  => MIXED: fewer_busts={fewer_busts}, ci_tied_net={ci_not_worse}. See numbers above.")
    print()
    print(f"  Bootstrap metadata: N={N_BOOTSTRAP}, block_len={BLOCK_LEN}, seed={SEED}")


if __name__ == "__main__":
    main()
