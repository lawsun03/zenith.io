"""B42 2022 holdout confirmatory: 6-year pipeline vs 5-year."""
from __future__ import annotations
import csv, sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
from app.backtest.funded_sim import daily_pnls_from_equity, simulate_combines, simulate_xfa_chain

YEARS_5 = ["2021", "2023", "2024", "2025", "2026"]
YEARS_6 = ["2021", "2022", "2023", "2024", "2025", "2026"]
BASELINE = Decimal("50000")
HAIRCUT = Decimal("200")


def stitch(equity_dir: Path, prefix: str, years: list[str]) -> list:
    curve: list = []
    offset = Decimal("0")
    for year in years:
        p = equity_dir / f"{prefix}_{year}.csv"
        if not p.exists():
            print(f"  MISSING: {p}", file=sys.stderr)
            continue
        seg = []
        with p.open() as f:
            for row in csv.DictReader(f):
                seg.append((datetime.fromisoformat(row["ts"]), Decimal(row["equity"])))
        if not seg:
            continue
        if curve:
            offset = curve[-1][1] - BASELINE
        curve.extend((ts, eq + offset) for ts, eq in seg)
    return curve


def get_stats(equity_dir: Path, prefix: str, years: list[str], label: str) -> dict:
    curve = stitch(equity_dir, prefix, years)
    if not curve:
        return {}
    daily = daily_pnls_from_equity(curve)
    td = len(daily)
    c = simulate_combines(daily, haircut=HAIRCUT)
    x = simulate_xfa_chain(daily, haircut=HAIRCUT)
    apf = Decimal(str(td)) / Decimal(str(c["attempts"])) if c["attempts"] else Decimal("0")
    ppf = (Decimal(str(c["attempts"])) / Decimal(str(c["passes"]))
           if c["passes"] else Decimal("inf"))
    cpf = ppf * apf
    rfpf = ppf * Decimal("150")
    xapf = (Decimal(str(td)) / Decimal(str(x["accounts"]))
            if x["accounts"] else Decimal("0"))
    npf = x["net_payouts"] / Decimal(str(x["accounts"])) if x["accounts"] else Decimal("0")
    sust_sa = (Decimal(str(c["passes"])) / Decimal(str(x["busts"]))
               if x["busts"] else Decimal("inf"))
    print(
        f"  {label}: passes={c['passes']}/{c['attempts']} "
        f"({float(apf):.1f}d/att, {float(cpf):.1f}d/funded) "
        f"xfa busts={x['busts']}/{x['accounts']} "
        f"net/acct=${float(npf):.0f} sust_sa={float(sust_sa):.2f}x"
    )
    return {
        "passes": c["passes"], "busts": x["busts"],
        "cpf": float(cpf), "rfpf": float(rfpf),
        "xapf": float(xapf), "npf": float(npf),
    }


def pipeline(a: dict, b: dict, label: str) -> None:
    rc = Decimal(str(a["rfpf"]))
    xn = Decimal(str(b["npf"]))
    nc = xn - rc
    cd = Decimal(str(a["cpf"])) + Decimal(str(b["xapf"]))
    nm = nc / cd * 21 if cd else Decimal("0")
    sust = (Decimal(str(a["passes"])) / Decimal(str(b["busts"]))
            if b["busts"] else Decimal("inf"))
    print(
        f"  {label}: ${float(nm):.0f}/mo sust={float(sust):.2f}x "
        f"(A passes={a['passes']}, B busts={b['busts']})"
    )


def main() -> None:
    B42 = _REPO_ROOT / "research" / "equity_b42"
    B21 = _REPO_ROOT / "research" / "equity_b21"

    print("=== Phase A standalone ===")
    a5 = get_stats(B42, "deployed_r1p0", YEARS_5, "5y Phase A deployed r1.0")
    a6 = get_stats(B42, "deployed_r1p0", YEARS_6, "6y Phase A deployed r1.0 (incl 2022)")

    print()
    print("=== Phase B standalone ===")
    b5 = get_stats(B21, "orb_reentry_r0p75", YEARS_5, "5y Phase B ORB-reentry r0.75")
    b6 = get_stats(B21, "orb_reentry_r0p75", YEARS_6, "6y Phase B ORB-reentry r0.75 (incl 2022)")

    print()
    print("=== Two-phase pipeline ===")
    pipeline(a5, b5, "5y deployed Phase A -> 5y ORB Phase B [B42 main result]")
    pipeline(a6, b6, "6y deployed Phase A -> 6y ORB Phase B [2022 holdout]")

    print()
    print("2022 Phase A PF (deployed combined r1.0): 0.9337 -- net -$11,824 (loss-making drought year)")
    print("2022 Phase B PF (ORB-reentry r0.75):     1.0724 -- net +$4,096 (ORB holds up in 2022)")


if __name__ == "__main__":
    main()
