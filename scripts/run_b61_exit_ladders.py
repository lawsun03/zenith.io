"""
B61 driver: Excursion-ladder exit variants benchmarked on Phase A (iFVG deployed
config) and Phase B (ORB-reentry r=0.75).

Grid tested:
  Phase A (iFVG): control=deployed(partial_r=1.5), be@1.5R, partial@2.0R,
                  partial@2.5R, be@1.5R+partial@2.0R
  Phase B (ORB):  control=B21(partial_r=0), be@1.5R, partial@2.0R,
                  be@1.5R+partial@2.0R
  (partial@2.5R for ORB r_multiple=2.5 = at-target price = no-op; omitted)

Prior: Lesson 20 — be_trail_r=1.0 killed two-thrust winners on both strategies.
This tests 1.5R as the boundary. Partials at 2.0R/2.5R are softer exits.
Stop rule: every ladder worse on BOTH $/mo AND sustainability vs B42 → REJECT.

Baseline:
  B42 = $549/mo, sust 3.23x (deployed Phase A + B21 Phase B)

Usage:
    python scripts/run_b61_exit_ladders.py
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
    daily_pnls_from_equity,
    simulate_combines,
    simulate_xfa_chain,
)

EQUITY_DIR_B21 = _REPO_ROOT / "research" / "equity_b21"
EQUITY_DIR_B42 = _REPO_ROOT / "research" / "equity_b42"
EQUITY_DIR_B61 = _REPO_ROOT / "research" / "equity_b61"
YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE    = Decimal("50000")
RESET_FEE   = Decimal("150")
DAYS_PER_MO = Decimal("21")
HAIRCUT     = Decimal("200")

B42_NET_MO   = 549.0
B42_SUST     = 3.23

BARS_YEARLY  = _REPO_ROOT / "bars" / "yearly"
EQUITY_EXP   = _REPO_ROOT / "scripts" / "equity_export.py"
PYTHON       = sys.executable


def _run_equity_export(bars_path: Path, extra_args: list[str], out_path: Path) -> None:
    cmd = [
        PYTHON, str(EQUITY_EXP),
        "--bars", str(bars_path),
        "--instrument", "MNQ",
        "--timeframe", "5min",
        "--out", str(out_path),
    ] + extra_args
    print(f"  Generating {out_path.name}...", end=" ", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
    if r.returncode != 0:
        print(f"ERROR\n{r.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)
    print("done")


def _ensure_equity_csvs() -> None:
    """Generate missing B61 equity CSVs for all exit variants."""
    EQUITY_DIR_B61.mkdir(parents=True, exist_ok=True)
    print("=== Generating B61 exit-variant equity CSVs (skips if already present) ===")

    # Phase A: iFVG deployed config (bot_config.json) + exit-mode overrides.
    # Deployed has: r_multiple=3.5, stop_buffer=3.0, min_absolute_body=5.0, partial_r=1.5.
    ifvg_base  = ["--risk-pct", "1.0"]
    ifvg_variants = [
        ("be15",     ifvg_base + ["--partial-r", "0",   "--set", "be_trail_r=1.5"]),
        ("p20",      ifvg_base + ["--partial-r", "2.0"]),
        ("p25",      ifvg_base + ["--partial-r", "2.5"]),
        ("be15_p20", ifvg_base + ["--partial-r", "2.0", "--set", "be_trail_r=1.5"]),
    ]
    for year in YEARS:
        bars = BARS_YEARLY / f"bars_MNQ_dbv_{year}.csv"
        for tag, extra in ifvg_variants:
            out = EQUITY_DIR_B61 / f"ifvg_{tag}_r1p0_{year}.csv"
            if out.exists():
                print(f"  Skipping {out.name} (exists)")
            else:
                _run_equity_export(bars, extra, out)

    # Phase B: ORB-reentry r=0.75 + exit-mode overrides.
    orb_base = [
        "--risk-pct", "0.75",
        "--set", "engine=orb",
        "--set", "orb_r_multiple=2.5",
        "--set", "orb_reentry_after_stop=True",
    ]
    orb_variants = [
        ("be15",     orb_base + ["--partial-r", "0",   "--set", "be_trail_r=1.5"]),
        ("p20",      orb_base + ["--partial-r", "2.0"]),
        ("be15_p20", orb_base + ["--partial-r", "2.0", "--set", "be_trail_r=1.5"]),
    ]
    for year in YEARS:
        bars = BARS_YEARLY / f"bars_MNQ_dbv_{year}.csv"
        for tag, extra in orb_variants:
            out = EQUITY_DIR_B61 / f"orb_{tag}_r0p75_{year}.csv"
            if out.exists():
                print(f"  Skipping {out.name} (exists)")
            else:
                _run_equity_export(bars, extra, out)
    print()


def _stitch(equity_dir: Path, prefix: str) -> list[tuple[datetime, Decimal]]:
    """Stitch per-year equity CSVs into a single continuous curve (2022 excluded)."""
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in YEARS:
        p = equity_dir / f"{prefix}_{year}.csv"
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


def _phase_stats(equity_dir: Path, prefix: str, label: str) -> dict:
    """Return combine + XFA stats for a stitched equity curve."""
    curve = _stitch(equity_dir, prefix)
    if not curve:
        return {}
    daily = daily_pnls_from_equity(curve)
    td = len(daily)
    c = simulate_combines(daily, haircut=HAIRCUT)
    x = simulate_xfa_chain(daily, haircut=HAIRCUT)
    avg_d  = Decimal(td) / Decimal(c["attempts"])  if c["attempts"] else Decimal(0)
    apf    = Decimal(c["attempts"]) / Decimal(c["passes"]) if c["passes"] else Decimal("inf")
    cpf    = apf * avg_d
    rfpf   = apf * RESET_FEE
    afd    = Decimal(td) / Decimal(x["accounts"]) if x["accounts"] else Decimal(0)
    npf    = x["net_payouts"] / Decimal(x["accounts"]) if x["accounts"] else Decimal(0)
    sust   = (Decimal(c["passes"]) / Decimal(x["busts"])
               if x["busts"] else Decimal("inf"))
    return {
        "label": label,
        "c_passes": c["passes"], "c_attempts": c["attempts"],
        "c_avg_days": float(avg_d), "c_days_per_funded": float(cpf),
        "c_reset_fee": float(rfpf),
        "x_accounts": x["accounts"], "x_busts": x["busts"],
        "x_net_per_account": float(npf), "x_avg_days": float(afd),
        "sustainability": float(sust),
    }


def _pipeline(a: dict, b: dict) -> dict:
    """Two-phase pipeline economics: Phase A combine → Phase B funded."""
    rc   = Decimal(str(a["c_reset_fee"]))
    xn   = Decimal(str(b["x_net_per_account"]))
    npc  = xn - rc
    cd   = Decimal(str(a["c_days_per_funded"]))
    fd   = Decimal(str(b["x_avg_days"]))
    cyc  = cd + fd
    npm  = (npc / cyc) * DAYS_PER_MO if cyc else Decimal(0)
    sust = (Decimal(a["c_passes"]) / Decimal(b["x_busts"])
            if b["x_busts"] else Decimal("inf"))
    return {
        "reset_cost": float(rc), "xfa_net": float(xn),
        "net_per_cycle": float(npc), "cycle_days": float(cyc),
        "net_per_month": float(npm), "sustainability": float(sust),
    }


def main() -> None:
    _ensure_equity_csvs()

    # ── Phase A ──────────────────────────────────────────────────────────────
    print("=== Phase A: iFVG exit variants ===")
    pa_cfgs = [
        (EQUITY_DIR_B42, "deployed_r1p0",      "A-ctrl  (partial@1.5R)"),
        (EQUITY_DIR_B61, "ifvg_be15_r1p0",     "A-be15  (BE@1.5R, no partial)"),
        (EQUITY_DIR_B61, "ifvg_p20_r1p0",      "A-p20   (partial@2.0R)"),
        (EQUITY_DIR_B61, "ifvg_p25_r1p0",      "A-p25   (partial@2.5R)"),
        (EQUITY_DIR_B61, "ifvg_be15_p20_r1p0", "A-be+p20 (BE@1.5R+partial@2.0R)"),
    ]
    a_stats: dict[str, dict] = {}
    for ed, pfx, lbl in pa_cfgs:
        s = _phase_stats(ed, pfx, lbl)
        if not s:
            print(f"  MISSING: {lbl}")
            continue
        a_stats[lbl] = s
        print(f"  {lbl:<32} combine {s['c_passes']:>2}/{s['c_attempts']:>2} "
              f"({s['c_avg_days']:.1f}d/attempt, ${s['c_reset_fee']:.0f}/funded)  "
              f"xfa {s['x_busts']:>2}/{s['x_accounts']:>2} busts  "
              f"sust {s['sustainability']:.2f}x")

    # ── Phase B ──────────────────────────────────────────────────────────────
    print()
    print("=== Phase B: ORB-reentry r=0.75 exit variants ===")
    pb_cfgs = [
        (EQUITY_DIR_B21, "orb_reentry_r0p75",   "B-ctrl  (partial@0)"),
        (EQUITY_DIR_B61, "orb_be15_r0p75",       "B-be15  (BE@1.5R)"),
        (EQUITY_DIR_B61, "orb_p20_r0p75",        "B-p20   (partial@2.0R)"),
        (EQUITY_DIR_B61, "orb_be15_p20_r0p75",   "B-be+p20 (BE@1.5R+partial@2.0R)"),
    ]
    b_stats: dict[str, dict] = {}
    for ed, pfx, lbl in pb_cfgs:
        s = _phase_stats(ed, pfx, lbl)
        if not s:
            print(f"  MISSING: {lbl}")
            continue
        b_stats[lbl] = s
        print(f"  {lbl:<32} xfa {s['x_busts']:>2}/{s['x_accounts']:>2} busts  "
              f"${s['x_net_per_account']:.0f}/acct  {s['x_avg_days']:.1f}d/acct  "
              f"sust {s['sustainability']:.2f}x")

    # ── Two-phase matrix ─────────────────────────────────────────────────────
    print()
    print("=" * 115)
    print("B61 PIPELINE MATRIX  (baseline B42: "
          f"${B42_NET_MO:.0f}/mo, sust {B42_SUST:.2f}x)")
    print("=" * 115)
    hdr = (f"{'Phase A -> Phase B':<62} {'Reset$':>7} {'XFA$':>7} "
           f"{'Net/cyc':>9} {'Cycle d':>7} {'$/mo':>8} {'Sust':>7}")
    print(hdr)
    print("-" * 115)

    rows = []
    for _, _, a_lbl in pa_cfgs:
        if a_lbl not in a_stats:
            continue
        for _, _, b_lbl in pb_cfgs:
            if b_lbl not in b_stats:
                continue
            econ = _pipeline(a_stats[a_lbl], b_stats[b_lbl])
            rows.append((econ["net_per_month"], a_lbl, b_lbl, econ))

    rows.sort(key=lambda r: -r[0])
    for npm, a_lbl, b_lbl, econ in rows:
        beats = (econ["net_per_month"] >= B42_NET_MO
                 and econ["sustainability"] >= B42_SUST)
        flag  = " *** BEATS B42" if beats else ""
        combo = f"{a_lbl} -> {b_lbl}"
        print(f"  {combo:<60} ${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>5.0f}  "
              f"${econ['net_per_cycle']:>7.0f}  {econ['cycle_days']:>6.1f}d  "
              f"${econ['net_per_month']:>6.0f}  {econ['sustainability']:>6.2f}x{flag}")

    # ── Verdict ───────────────────────────────────────────────────────────────
    print()
    print("=" * 115)
    print("VERDICT")
    print("=" * 115)
    candidates = [(npm, al, bl, e) for npm, al, bl, e in rows
                  if e["net_per_month"] >= B42_NET_MO and e["sustainability"] >= B42_SUST]
    if candidates:
        best_npm, best_a, best_b, best_e = candidates[0]
        print(f"  CANDIDATE: {best_a} → {best_b}")
        print(f"    ${best_e['net_per_month']:.0f}/mo, sust {best_e['sustainability']:.2f}x "
              f"— BEATS B42 (${B42_NET_MO:.0f}/mo, sust {B42_SUST:.2f}x) on BOTH metrics")
    else:
        print("  REJECTED: no exit variant beats B42 on both $/mo AND sustainability.")
        print("  Stop rule fires — exit ladders do not improve the NQ pipeline.")

    # ── Stop-rule explainer ───────────────────────────────────────────────────
    a_ctrl = a_stats.get("A-ctrl  (partial@1.5R)")
    b_ctrl = b_stats.get("B-ctrl  (partial@0)")
    if a_ctrl and b_ctrl:
        ctrl_econ = _pipeline(a_ctrl, b_ctrl)
        print(f"\n  Control A -> control B (re-derived): "
              f"${ctrl_econ['net_per_month']:.0f}/mo, sust {ctrl_econ['sustainability']:.2f}x")

    any_improves_either = any(
        e["net_per_month"] > B42_NET_MO or e["sustainability"] > B42_SUST
        for _, _, _, e in rows
    )
    if not any_improves_either:
        print("  All variants worse on BOTH metrics — stop rule: REJECT B61 exits.")
    else:
        improved = [(npm, al, bl, e) for npm, al, bl, e in rows
                    if e["net_per_month"] > B42_NET_MO or e["sustainability"] > B42_SUST]
        print(f"  {len(improved)} variant(s) improve at least one metric (see matrix above).")


if __name__ == "__main__":
    main()
