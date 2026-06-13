"""
B1 driver: funded-objective scoring of bench variants.

Per (variant, risk): runs scripts/equity_export.py per YEAR segment (each a
fresh $50k start so risk-%-of-equity sizing stays honest to account scale and
2022 stays excluded), concatenates the segment equity curves with rebasing
(segment k shifted so its $50k baseline continues from segment k-1's end),
then scores the stitched daily P&L with app.backtest.funded_sim at haircuts
0/200/400. Existing equity CSVs are reused (delete to re-run).

    python scripts/run_b1_funded.py --variants control,orb --risks 1.25
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.funded_sim import (daily_pnls_from_equity,
                                     simulate_combines, simulate_xfa_chain)

PY = str(_REPO_ROOT / ".venv" / "Scripts" / "python.exe")
OUT_DIR = _REPO_ROOT / "research" / "equity_b1"
YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")

VARIANTS: dict[str, dict] = {
    "control":    {"sets": ["engine=ifvg"], "trail": False},
    "frozen_atr": {"sets": ["engine=ifvg", "atr_ref_lag_bars=12"], "trail": False},
    "trail_1r":   {"sets": ["engine=ifvg"], "trail": True},
    "stop_cap":   {"sets": ["engine=ifvg", "max_stop_atr=6"], "trail": False},
    "orb":        {"sets": ["engine=orb", "orb_r_multiple=2.5"], "trail": False},
    "rs_invert":  {"sets": ["engine=regime_switch", "orb_r_multiple=2.5",
                            "rs_invert=True"], "trail": False},
}


def export_one(variant: str, risk: str, year: str) -> dict:
    tag = f"{variant}_r{risk.replace('.', 'p')}_{year}"
    out = OUT_DIR / f"{tag}.csv"
    meta = OUT_DIR / f"{tag}.meta.json"
    if out.exists() and meta.exists():
        return json.loads(meta.read_text())
    spec = VARIANTS[variant]
    cmd = [PY, str(_REPO_ROOT / "scripts" / "equity_export.py"),
           "--bars", str(_REPO_ROOT / "bars" / "yearly" / f"bars_MNQ_dbv_{year}.csv"),
           "--risk-pct", risk, "--out", str(out)]
    for s in spec["sets"]:
        cmd += ["--set", s]
    if spec["trail"]:
        cmd += ["--trail-1r"]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=_REPO_ROOT)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"{tag} failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    m = re.search(r"trades=(\d+), net=([-\d.]+), pf=([\d.]+|None)", r.stdout)
    info = {"tag": tag, "variant": variant, "risk": risk, "year": year,
            "trades": int(m.group(1)) if m else None,
            "net": m.group(2) if m else None,
            "pf": (None if not m or m.group(3) == "None" else float(m.group(3)))}
    meta.write_text(json.dumps(info))
    print(f"  done {tag}: trades={info['trades']} net={info['net']} pf={info['pf']}",
          flush=True)
    return info


def stitch(variant: str, risk: str, years: list[str]) -> list[tuple[datetime, Decimal]]:
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in years:
        tag = f"{variant}_r{risk.replace('.', 'p')}_{year}"
        seg: list[tuple[datetime, Decimal]] = []
        with (OUT_DIR / f"{tag}.csv").open(newline="") as f:
            for row in csv.DictReader(f):
                seg.append((datetime.fromisoformat(row["ts"]), Decimal(row["equity"])))
        if not seg:
            continue
        if curve:
            offset = curve[-1][1] - BASELINE
        curve.extend((ts, eq + offset) for ts, eq in seg)
    return curve


def score(variant: str, risk: str, years: list[str], metas: list[dict]) -> dict:
    curve = stitch(variant, risk, years)
    daily = daily_pnls_from_equity(curve)
    by_haircut = {}
    for h in ("0", "200", "400"):
        hc = Decimal(h)
        c = simulate_combines(daily, haircut=hc)
        x = simulate_xfa_chain(daily, haircut=hc)
        by_haircut[h] = {
            "combine": {k: (str(v) if isinstance(v, Decimal) else v)
                        for k, v in c.items()},
            "xfa": {k: (str(v) if isinstance(v, Decimal) else v)
                    for k, v in x.items()},
        }
    per_year = {m["year"]: {"trades": m["trades"], "net": m["net"], "pf": m["pf"]}
                for m in metas}
    total_net = sum(Decimal(m["net"]) for m in metas if m["net"] is not None)
    return {"variant": variant, "risk": risk, "years": years,
            "per_year": per_year, "total_net": str(total_net),
            "funded": by_haircut}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--risks", default="1.25")
    ap.add_argument("--years", default=",".join(YEARS))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default="research/b1_results.json")
    args = ap.parse_args()

    variants = args.variants.split(",")
    risks = args.risks.split(",")
    years = args.years.split(",")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    tasks = [(v, r, y) for v in variants for r in risks for y in years]
    print(f"{len(tasks)} export tasks, {args.workers} workers")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {(v, r, y): pool.submit(export_one, v, r, y) for v, r, y in tasks}
        metas = {k: f.result() for k, f in futs.items()}

    results = []
    for v in variants:
        for r in risks:
            res = score(v, r, years, [metas[(v, r, y)] for y in years])
            results.append(res)
            x200 = res["funded"]["200"]["xfa"]
            c200 = res["funded"]["200"]["combine"]
            print(f"{v:11s} r{r}: net5seg {res['total_net']:>10s} | "
                  f"XFA net ${Decimal(x200['net_payouts']):.0f} "
                  f"busts {x200['busts']}/{x200['accounts']} | "
                  f"combine {c200['passes']}/{c200['attempts']} (h200)")

    out = _REPO_ROOT / args.out
    existing = json.loads(out.read_text()) if out.exists() else []
    keyed = {(e["variant"], e["risk"]): e for e in existing}
    for res in results:
        keyed[(res["variant"], res["risk"])] = res
    out.write_text(json.dumps(list(keyed.values()), indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    main()
