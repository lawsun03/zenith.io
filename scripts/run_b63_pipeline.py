"""
B63 pipeline benchmark — funded-only sizing/routing variants.

(b) Tests iFVG long-only close-mode configs as Phase B (vs B21 ORB-reentry reference).
    Uses existing equity_b28 CSVs (r1.0 / r1.25). Also tests London+NY AM variant if
    CSVs are present.

(a) Phase 1 cheap check: from mfe_mae data, does second-trade PF differ when first
    trade won vs lost? If ratio < 1.2 -> Phase 1 NO-GO; reject without code.
    If Phase 1 GO: applies +0.5x boost post-processing to generate modified equity
    curve, runs funded_sim, compares to B21.

Stop rule: both (a) and (b) must beat B21 pipeline reference (sust >= 1.0 AND
better on $/mo or sust). Neither beating B21 -> reject, B21 stands.

Phase A: equity_b42/deployed_r1p0 (B42 deployed config, 42 passes over 5y).
Phase B ref: equity_b21/orb_reentry_r0p75 (B21 ORB-reentry, sust 3.23x, $549/mo).
"""
from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from app.backtest.funded_sim import (
    daily_pnls_from_equity,
    simulate_combines,
    simulate_xfa_chain,
)

EQUITY_B42 = _REPO / "research" / "equity_b42"
EQUITY_B21 = _REPO / "research" / "equity_b21"
EQUITY_B28 = _REPO / "research" / "equity_b28"
EQUITY_B63 = _REPO / "research" / "equity_b63"

YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 frozen holdout
HAIRCUT = Decimal("200")
RESET_FEE = Decimal("150")
TDAYS_PER_MONTH = Decimal("21")
STARTING = Decimal("50000")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def stitch(pdir: Path, prefix: str) -> list[tuple[datetime, Decimal]]:
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in YEARS:
        p = pdir / f"{prefix}_{year}.csv"
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
            offset = curve[-1][1] - STARTING
        curve.extend((ts, eq + offset) for ts, eq in seg)
    return curve


def pipeline_econ(
    c: dict, a_total_days: int,
    x: dict, b_total_days: int,
) -> dict:
    if not c["passes"] or not x["accounts"]:
        return {"npm": 0.0, "sust": 0.0, "reset": 0.0, "cycle_days": 0.0}
    att_per_funded = Decimal(str(c["attempts"])) / Decimal(str(c["passes"]))
    avg_a_days = Decimal(str(a_total_days)) / Decimal(str(c["attempts"]))
    avg_b_days = Decimal(str(b_total_days)) / Decimal(str(x["accounts"]))
    reset = att_per_funded * RESET_FEE
    net_per_acct = Decimal(str(float(x["net_payouts"]))) / Decimal(str(x["accounts"]))
    net_per_cycle = net_per_acct - reset
    cycle_days = att_per_funded * avg_a_days + avg_b_days
    npm = net_per_cycle / cycle_days * TDAYS_PER_MONTH if cycle_days else Decimal("0")
    sust = Decimal(str(c["passes"])) / Decimal(str(x["busts"])) if x["busts"] else Decimal("99")
    return {
        "npm": float(npm),
        "sust": float(sust),
        "reset": float(reset),
        "cycle_days": float(cycle_days),
        "net_per_acct": float(net_per_acct),
        "avg_b_days": float(avg_b_days),
    }


def print_result(label: str, c: dict, x: dict, econ: dict, *, ref_npm: float, ref_sust: float) -> None:
    beats_npm = econ["npm"] > ref_npm
    beats_sust = econ["sust"] > ref_sust
    both = beats_npm and beats_sust
    flag = " *** BOTH BETTER" if both else (" * one better" if (beats_npm or beats_sust) else " -- both worse")
    print(
        f"  {label:<45} "
        f"B={x['accounts']:>3} busts={x['busts']:>3} "
        f"$/acct=${econ['net_per_acct']:>5.0f} "
        f"avg_days={econ['avg_b_days']:>5.1f} "
        f"reset=${econ['reset']:>4.0f}  "
        f"npm=${econ['npm']:>5.0f}  sust={econ['sust']:>5.2f}x{flag}"
    )


# ---------------------------------------------------------------------------
# Part (a): early_win_boost logic
# ---------------------------------------------------------------------------

def apply_early_win_boost_day(day_trades: list[dict], boost: float = 0.5) -> list[float]:
    """
    B63(a): if first trade of the day won (pnl > 0), scale the SECOND trade's
    PnL by (1 + boost). All other trades and days are unchanged.

    Returns the list of PnL values for the day (same length as day_trades).
    """
    if len(day_trades) < 1:
        return []
    pnls = [t["pnl"] for t in day_trades]
    if len(pnls) >= 2 and pnls[0] > 0:
        pnls[1] = pnls[1] * (1.0 + boost)
    return pnls


def _load_mfe_mae_by_day() -> dict[str, list[dict]] | None:
    """Load iFVG+ORB combined trade data, group by trading day."""
    from collections import defaultdict
    mfe_orb = _REPO / "research" / "mfe_mae_orb_clean.csv"
    mfe_ifvg = _REPO / "research" / "mfe_mae_ifvg_clean.csv"
    if not mfe_orb.exists() or not mfe_ifvg.exists():
        return None
    trades: list[dict] = []
    for path in [mfe_orb, mfe_ifvg]:
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                if row.get("year", "2022") == "2022":
                    continue
                trades.append({
                    "day": row["entry_ts"][:10],
                    "pnl": float(row["realized_pnl"]),
                    "ts": row["entry_ts"],
                })
    by_day: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_day[t["day"]].append(t)
    for day_trades in by_day.values():
        day_trades.sort(key=lambda t: t["ts"])
    return by_day


def phase1_early_win_check() -> tuple[float, float, int]:
    """
    Load mfe_mae combined data, group by trading day, compare PF of second
    trade when first trade won (realized_pnl > 0) vs when first trade lost.

    Returns (pf_after_win, pf_after_loss, n_second_trades).
    """
    by_day = _load_mfe_mae_by_day()
    if by_day is None:
        print("  WARNING: mfe_mae files not found; skipping Phase 1 (a) check", file=sys.stderr)
        return 0.0, 0.0, 0

    after_win: list[float] = []
    after_loss: list[float] = []
    for day_trades in by_day.values():
        if len(day_trades) < 2:
            continue
        if day_trades[0]["pnl"] > 0:
            after_win.append(day_trades[1]["pnl"])
        else:
            after_loss.append(day_trades[1]["pnl"])

    def pf(pnls: list[float]) -> float:
        wins = sum(p for p in pnls if p > 0)
        losses = abs(sum(p for p in pnls if p < 0))
        return wins / losses if losses > 0 else (1.0 if wins > 0 else 0.0)

    pf_win = pf(after_win)
    pf_loss = pf(after_loss)
    n = len(after_win) + len(after_loss)
    return pf_win, pf_loss, n


def phase2_early_win_boost_sim() -> tuple[dict, dict] | None:
    """
    B63(a) Phase 2: apply early_win_boost to combined-engine daily PnL and
    run funded_sim. Returns (xfa_base, xfa_boosted) or None if no data.

    Phase B engine: combined (iFVG+ORB), using mfe_mae data for trade sequences.
    Daily PnL base = sum of all trades. Boosted = same but second trade × 1.5
    on days where first trade won.
    """
    by_day = _load_mfe_mae_by_day()
    if by_day is None:
        return None

    # Build base and boosted daily P&L sequences (sorted by date)
    base_daily: list[tuple[datetime, Decimal]] = []
    boost_daily: list[tuple[datetime, Decimal]] = []
    for day_str in sorted(by_day.keys()):
        day_trades = by_day[day_str]
        base_pnl = sum(t["pnl"] for t in day_trades)
        boosted_pnls = apply_early_win_boost_day(day_trades, boost=0.5)
        boosted_pnl = sum(boosted_pnls)
        # Use first entry_ts as day anchor
        ts = datetime.fromisoformat(day_trades[0]["ts"])
        base_daily.append((ts, Decimal(str(round(base_pnl, 4)))))
        boost_daily.append((ts, Decimal(str(round(boosted_pnl, 4)))))

    xfa_base = simulate_xfa_chain(base_daily, haircut=HAIRCUT)
    xfa_boost = simulate_xfa_chain(boost_daily, haircut=HAIRCUT)
    return xfa_base, xfa_boost


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 80)
    print("B63 PIPELINE BENCHMARK — funded-only sizing/routing variants")
    print("=" * 80)

    # Phase A: B42 deployed
    print("\nLoading Phase A (B42)...")
    curve_a = stitch(EQUITY_B42, "deployed_r1p0")
    daily_a = daily_pnls_from_equity(curve_a)
    ca = simulate_combines(daily_a, haircut=HAIRCUT)
    avg_a_days = len(daily_a) / ca["attempts"] if ca["attempts"] else 0
    print(
        f"  B42 Phase A: passes={ca['passes']} / attempts={ca['attempts']} "
        f"busts={ca['busts']}  avg={avg_a_days:.1f}d/attempt"
    )

    # Phase B reference: B21 ORB-reentry r0.75
    print("\nLoading Phase B reference (B21 ORB-reentry r0.75)...")
    curve_b21 = stitch(EQUITY_B21, "orb_reentry_r0p75")
    daily_b21 = daily_pnls_from_equity(curve_b21)
    xb21 = simulate_xfa_chain(daily_b21, haircut=HAIRCUT)
    econ_ref = pipeline_econ(ca, len(daily_a), xb21, len(daily_b21))
    ref_npm, ref_sust = econ_ref["npm"], econ_ref["sust"]
    print_result("B21 ORB r0.75 [REFERENCE]", ca, xb21, econ_ref, ref_npm=ref_npm, ref_sust=ref_sust)

    # -----------------------------------------------------------------------
    # Part (b): iFVG long-only close-mode as Phase B
    # -----------------------------------------------------------------------
    print("\n--- Part (b): iFVG long-only close-mode as Phase B ---")
    b_configs: list[tuple[str, Path, str]] = [
        ("iFVG-LO-close r1.0%",   EQUITY_B28, "longonly_close_r1p0"),
        ("iFVG-LO-close r1.25%",  EQUITY_B28, "longonly_close_r1p25"),
    ]
    # London+NY AM variant
    eq_b63_lo_kz = EQUITY_B63 / "lo_kz_r1p0"
    if eq_b63_lo_kz.exists():
        b_configs.append(("iFVG-LO-London+NYAM r1.0%", eq_b63_lo_kz, "lo_kz_r1p0"))

    results_b: list[tuple[str, dict, dict]] = []
    for label, pdir, prefix in b_configs:
        curve = stitch(pdir, prefix)
        if not curve:
            print(f"  SKIP: no data for {label}")
            continue
        daily = daily_pnls_from_equity(curve)
        x = simulate_xfa_chain(daily, haircut=HAIRCUT)
        econ = pipeline_econ(ca, len(daily_a), x, len(daily))
        print_result(f"(b) {label}", ca, x, econ, ref_npm=ref_npm, ref_sust=ref_sust)
        results_b.append((label, x, econ))

    # -----------------------------------------------------------------------
    # Part (a): Phase 1 cheap check — early_win_boost signal
    # -----------------------------------------------------------------------
    print("\n--- Part (a): Phase 1 check — second-trade PF conditional on first-trade win ---")
    pf_win, pf_loss, n = phase1_early_win_check()
    if n == 0:
        print("  Phase 1 check SKIPPED (no mfe_mae data).")
        phase1_go = False
    else:
        ratio = pf_win / pf_loss if pf_loss > 0 else 0.0
        print(f"  n_second_trades={n}  PF_after_win={pf_win:.3f}  PF_after_loss={pf_loss:.3f}  ratio={ratio:.3f}")
        phase1_go = ratio >= 1.2
        verdict_p1 = "GO" if phase1_go else "NO-GO"
        print(f"  Phase 1 (a): {verdict_p1} (threshold ratio >= 1.20)")

    # -----------------------------------------------------------------------
    # Summary and stop-rule verdict
    # -----------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("VERDICT SUMMARY")
    print("=" * 80)
    print(f"  Reference: B42->B21  npm=${ref_npm:.0f}/mo  sust={ref_sust:.2f}x")
    print()

    any_candidate = False
    for label, x, econ in results_b:
        beats = econ["npm"] > ref_npm or econ["sust"] > ref_sust
        both = econ["npm"] > ref_npm and econ["sust"] > ref_sust
        tag = "CANDIDATE" if both else ("partial" if beats else "REJECTED")
        print(f"  (b) {label:<40}: npm=${econ['npm']:.0f}/mo sust={econ['sust']:.2f}x -> {tag}")
        if both:
            any_candidate = True

    # -----------------------------------------------------------------------
    # Part (a) Phase 2: early_win_boost funded_sim
    # -----------------------------------------------------------------------
    if phase1_go:
        print("\n--- Part (a) Phase 2: early_win_boost funded simulation ---")
        p2 = phase2_early_win_boost_sim()
        if p2 is None:
            print("  SKIP: no mfe_mae data for Phase 2.")
            phase2_econ_base = phase2_econ_boost = None
        else:
            xb, xk = p2
            avg_days_b = len(xb) if False else 0  # placeholder
            # Compute pipeline econ using mfe_mae day count for Phase B
            by_day = _load_mfe_mae_by_day()
            mfemae_days = len(by_day) if by_day else 0
            econ_b_base = pipeline_econ(ca, len(daily_a), xb, mfemae_days)
            econ_b_boost = pipeline_econ(ca, len(daily_a), xk, mfemae_days)
            phase2_econ_base = econ_b_base
            phase2_econ_boost = econ_b_boost
            print(
                f"  combined no-boost:  accounts={xb['accounts']:>3} busts={xb['busts']:>3} "
                f"$/acct=${float(xb['net_payouts'])/xb['accounts'] if xb['accounts'] else 0:.0f} "
                f"npm=${econ_b_base['npm']:.0f}  sust={econ_b_base['sust']:.2f}x"
            )
            print(
                f"  combined +boost:    accounts={xk['accounts']:>3} busts={xk['busts']:>3} "
                f"$/acct=${float(xk['net_payouts'])/xk['accounts'] if xk['accounts'] else 0:.0f} "
                f"npm=${econ_b_boost['npm']:.0f}  sust={econ_b_boost['sust']:.2f}x"
            )
            boost_beats_base_npm = econ_b_boost["npm"] > econ_b_base["npm"]
            boost_beats_b21_npm = econ_b_boost["npm"] > ref_npm
            boost_beats_b21_sust = econ_b_boost["sust"] > ref_sust
            boost_sust_ok = econ_b_boost["sust"] >= 1.0
            print(
                f"  boost vs no-boost: npm delta ${econ_b_boost['npm']-econ_b_base['npm']:+.0f}  "
                f"sust delta {econ_b_boost['sust']-econ_b_base['sust']:+.2f}x"
            )
            if boost_beats_b21_npm and boost_sust_ok:
                a_verdict = "CANDIDATE (beats B21 $/mo, sust >= 1.0)"
                any_candidate = True
            elif boost_sust_ok and boost_beats_b21_sust:
                a_verdict = "CANDIDATE (beats B21 sust, sust >= 1.0)"
                any_candidate = True
            else:
                a_verdict = "REJECTED (stop rule: doesn't beat B21 on both metrics)"
            print(f"  (a) verdict: {a_verdict}")
    else:
        print(f"  (a) Phase 1 NO-GO — ratio {pf_win/pf_loss if pf_loss>0 else 0:.3f} < 1.20; reject without code")
        phase2_econ_base = phase2_econ_boost = None

    print()
    if not any_candidate:
        print("  STOP RULE TRIGGERED: neither (a) nor (b) beat B21 baseline. B63 REJECTED.")
        print("  B21 two-phase pipeline remains best: iFVG Phase A + ORB-reentry Phase B.")
    else:
        print("  CANDIDATE(S) found. Review sustainability and recommend.")

    print()
    print("Definitions:")
    print("  sust = Phase-A-passes / Phase-B-busts")
    print("  npm  = (XFA$/acct - reset_fee/acct) * 21 / cycle_days")
    print("  Haircut $200. 2022 excluded (holdout).")


if __name__ == "__main__":
    main()
