"""B58 funded-pipeline analysis: confluence sizing vs control."""
from __future__ import annotations

import csv
import sys
from decimal import Decimal
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.funded_sim import daily_pnls_from_equity, simulate_combines, simulate_xfa_chain

YEARS = ["2021", "2023", "2024", "2025", "2026"]
BASELINE = Decimal("50000")
HAIRCUT = Decimal("200")
TRADING_DAYS_PER_MONTH = Decimal("21")


def stitch(eq_dir: Path, prefix: str) -> list:
    curve: list = []
    offset = Decimal("0")
    for yr in YEARS:
        p = eq_dir / f"{prefix}_{yr}.csv"
        if not p.exists():
            print(f"  MISSING: {p}", file=sys.stderr)
            continue
        seg: list = []
        with p.open() as f:
            for row in csv.DictReader(f):
                seg.append((datetime.fromisoformat(row["ts"]), Decimal(row["equity"])))
        if not seg:
            continue
        if curve:
            offset = curve[-1][1] - BASELINE
        curve.extend((ts, eq + offset) for ts, eq in seg)
    return curve


def phase_stats(eq_dir: Path, prefix: str) -> dict | None:
    curve = stitch(eq_dir, prefix)
    if not curve:
        return None
    daily = daily_pnls_from_equity(curve)
    c = simulate_combines(daily, haircut=HAIRCUT)
    x = simulate_xfa_chain(daily, haircut=HAIRCUT)
    return {"c": c, "x": x, "td": len(daily)}


def pipeline_econ(s: dict) -> dict:
    c, x, td = s["c"], s["x"], s["td"]
    att = c["attempts"] or 1
    passes = c["passes"] or 1
    busts = x["busts"] or 1
    avg_days = Decimal(str(td)) / Decimal(str(att))
    att_per_funded = Decimal(str(att)) / Decimal(str(passes))
    reset_fee = att_per_funded * Decimal("150")
    xfa_net = x["net_payouts"] / Decimal(str(x["accounts"])) if x["accounts"] else Decimal("0")
    avg_xfa_d = Decimal(str(td)) / Decimal(str(x["accounts"])) if x["accounts"] else Decimal("0")
    cycle_days = att_per_funded * avg_days + avg_xfa_d
    net_month = (xfa_net - reset_fee) / cycle_days * TRADING_DAYS_PER_MONTH if cycle_days else Decimal("0")
    sust = Decimal(str(passes)) / Decimal(str(busts))
    return {
        "passes": passes, "att": att, "reset_fee": reset_fee,
        "xfa_net": xfa_net, "net_month": net_month, "sust": sust,
        "busts": busts, "xfa_accounts": x["accounts"],
    }


def main() -> None:
    eq_b58 = _REPO_ROOT / "research" / "equity_b58"
    eq_b57 = _REPO_ROOT / "research" / "equity_b57"
    eq_b42 = _REPO_ROOT / "research" / "equity_b42"

    configs = [
        ("B58 confluence r=2.5", eq_b58, "b58_r2p5"),
        ("B57 r=2.5 (control)",  eq_b57, "r2p5"),
        ("B42 deployed (r=3.5)", eq_b42, "deployed_r1p0"),
    ]

    print("=" * 85)
    print("B58 FUNDED PIPELINE: confluence sizing vs flat-size control")
    print(f"{'Variant':<28} {'A:pass/att':>12} {'Reset$':>7} {'XFA$/acct':>10} {'Net/mo':>8} {'Sust':>8}")
    print("-" * 85)

    results = []
    for label, eq_dir, prefix in configs:
        s = phase_stats(eq_dir, prefix)
        if s is None:
            print(f"{label:<28}  MISSING DATA")
            continue
        e = pipeline_econ(s)
        results.append((label, e))
        print(f"{label:<28} {e['passes']}/{e['att']:>3}        "
              f"${float(e['reset_fee']):>5.0f}  "
              f"${float(e['xfa_net']):>8.0f}  "
              f"${float(e['net_month']):>6.0f}  "
              f"{float(e['sust']):>6.2f}x")

    print("\nVERDICT:")
    baseline = next((e for lbl, e in results if "B42" in lbl), None)
    control = next((e for lbl, e in results if "B57" in lbl), None)
    for label, e in results:
        if baseline and "B42" not in label:
            vs = baseline
            mo_d = float(e["net_month"] - vs["net_month"])
            su_d = float(e["sust"] - vs["sust"])
            status = "BETTER" if mo_d > 0 and su_d > 0 else ("worse" if mo_d < 0 and su_d < 0 else "mixed")
            print(f"  {label}: ${float(e['net_month']):.0f}/mo, sust {float(e['sust']):.2f}x "
                  f"(vs B42: {'+' if mo_d >= 0 else ''}{mo_d:.0f}$/mo, "
                  f"{'+' if su_d >= 0 else ''}{su_d:.2f}x sust) [{status}]")
        else:
            print(f"  {label}: ${float(e['net_month']):.0f}/mo, sust {float(e['sust']):.2f}x [baseline]")
    print()

    # Stop-rule evaluation
    if control and results:
        b58_e = next((e for lbl, e in results if "B58" in lbl), None)
        if b58_e:
            print("STOP RULE (vs flat-size control B57 r=2.5):")
            mo_d = float(b58_e["net_month"] - control["net_month"])
            su_d = float(b58_e["sust"] - control["sust"])
            if mo_d < 0 and su_d < 0:
                print("  REJECT: B58 confluence loses to flat-size on BOTH metrics.")
                print("  Document alongside B47 (other failed confluence approaches).")
            elif mo_d > 0 and su_d > 0:
                print("  CANDIDATE: B58 beats flat-size control on both metrics.")
            else:
                print(f"  MIXED: net/mo {'+' if mo_d >= 0 else ''}{mo_d:.0f}, "
                      f"sust {'+' if su_d >= 0 else ''}{su_d:.2f}x")


if __name__ == "__main__":
    main()
