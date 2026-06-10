"""Tests for SetupGrader.

Tests encode WHY each grade matters for trade selection.
Every test must fail if the graded criterion's logic is broken.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.broker.events import Bar
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.strategy.grader import SetupGrade, SetupGrader

BASE_TS = datetime(2026, 5, 28, 14, 30, tzinfo=timezone.utc)


def bar(i: int, o: str, h: str, l: str, c: str, tf: str = "30min") -> Bar:
    return Bar(
        instrument="MGC", timeframe=tf,
        ts=BASE_TS + timedelta(minutes=i * 30),
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=100,
    )


def fvg(side: str, low: str, high: str) -> FairValueGap:
    return FairValueGap(
        side=side, low=Decimal(low), high=Decimal(high), created_at=BASE_TS
    )


def make_signal(
    side: str = "short",
    entry: str = "2405",
    stop: str = "2410",
    target: str = "2395",
    sweep_extreme: str = "2408",
    fvg_low: str = "2401",
    fvg_high: str = "2403",
    rationale: str = "NY AM: test signal",
    killzone: str = "NY AM",
    sweep_bar_range: str | None = None,
):
    from app.strategy.composer import Signal
    return Signal(
        instrument="MGC",
        side=side,
        entry=Decimal(entry),
        stop=Decimal(stop),
        target=Decimal(target),
        created_at=BASE_TS,
        killzone=killzone,
        sweep_pattern="B_one_bar",
        sweep_extreme=Decimal(sweep_extreme),
        fvg_low=Decimal(fvg_low),
        fvg_high=Decimal(fvg_high),
        rationale=rationale,
        sweep_bar_range=Decimal(sweep_bar_range) if sweep_bar_range else None,
    )


def make_disp(
    body_to_atr: str = "1.8",
    body_to_range: str = "0.7",
    atr: str = "2.0",
    side: str = "bearish",
) -> DisplacementEvent:
    body = Decimal(body_to_atr) * Decimal(atr)
    bar_range = body / Decimal(body_to_range) if Decimal(body_to_range) > 0 else body
    b2 = Bar(
        instrument="MGC", timeframe="1min", ts=BASE_TS,
        open=Decimal("2410"),
        high=Decimal("2410") + (bar_range - body) / 2,
        low=Decimal("2410") - body - (bar_range - body) / 2,
        close=Decimal("2406") - body,
        volume=100,
    )
    return DisplacementEvent(
        side=side,
        displacement_bar=b2,
        body_size=body,
        atr_at_event=Decimal(atr),
        body_to_atr=Decimal(body_to_atr),
        fvg=fvg("bullish", "2401", "2403"),
    )


def grader_with_htf(
    sess_high: str = "2415",
    sess_low: str = "2390",
    swing_highs: list[str] | None = None,
    swing_lows: list[str] | None = None,
) -> SetupGrader:
    g = SetupGrader()
    g.update_htf_swings(
        highs=[Decimal(h) for h in (swing_highs or ["2420"])],
        lows=[Decimal(l) for l in (swing_lows or ["2385"])],
    )
    g._session_ranges["NY AM"] = (Decimal(sess_high), Decimal(sess_low))
    g._last_killzone = "NY AM"
    return g


# ── Session range ────────────────────────────────────────────────────

class TestSessionRange:
    def test_session_range_expands_within_killzone(self):
        g = SetupGrader()
        g.update_session_range(bar(0, "2400", "2405", "2398", "2402", "1min"), "NY AM")
        g.update_session_range(bar(1, "2402", "2407", "2401", "2404", "1min"), "NY AM")
        high, low = g.session_range("NY AM")
        assert high == Decimal("2407")
        assert low == Decimal("2398")

    def test_session_range_resets_on_killzone_change(self):
        g = SetupGrader()
        g.update_session_range(bar(0, "2400", "2410", "2395", "2405", "1min"), "London")
        g.update_session_range(bar(1, "2405", "2406", "2403", "2404", "1min"), "NY AM")
        high, low = g.session_range("NY AM")
        assert high == Decimal("2406")
        assert low == Decimal("2403")

    def test_no_update_outside_killzone(self):
        g = SetupGrader()
        g.update_session_range(bar(0, "2400", "2405", "2398", "2402", "1min"), None)
        assert g.session_range("NY AM") is None


# ── Delivery FVG (Correction 6) ──────────────────────────────────────

class TestDeliveryFVG:
    def _make_30min_bars_with_bearish_fvg(self) -> list[Bar]:
        """Three 30min bars forming a bearish FVG [2408, 2410]."""
        return [
            bar(0, "2411", "2412", "2410", "2411"),  # b1: low=2410
            bar(1, "2410", "2411", "2408", "2409"),  # b2
            bar(2, "2405", "2408", "2404", "2406"),  # b3: high=2408 < b1.low=2410 → FVG [2408,2410]
        ]

    def test_delivery_fvg_detected_with_correct_side_and_pd(self):
        """Short signal: needs BEARISH 30min FVG at sweep high, in premium."""
        g = grader_with_htf(sess_high="2415", sess_low="2395")
        # Session mid = (2415+2395)/2 = 2405; sweep_extreme=2408 > mid → premium
        g.update_delivery_fvgs(self._make_30min_bars_with_bearish_fvg())
        sig = make_signal(side="short", sweep_extreme="2409")
        disp = make_disp(atr="2.0")
        has, side, in_pd = g._check_delivery_fvg(sig, disp)
        assert has is True
        assert side == "bearish"
        assert in_pd is True

    def test_delivery_fvg_not_detected_wrong_side(self):
        """Short signal needs bearish FVG, but only bullish FVGs exist."""
        g = grader_with_htf()
        # Only bullish FVG in delivery list
        g._delivery_fvgs = [fvg("bullish", "2407", "2409")]
        sig = make_signal(side="short", sweep_extreme="2408")
        disp = make_disp(atr="2.0")
        has, side, in_pd = g._check_delivery_fvg(sig, disp)
        assert has is False

    def test_delivery_fvg_not_detected_sweep_far_from_fvg(self):
        """Sweep extreme far from any 30min FVG."""
        g = grader_with_htf()
        g.update_delivery_fvgs(self._make_30min_bars_with_bearish_fvg())
        sig = make_signal(side="short", sweep_extreme="2450")  # far from FVG [2408,2410]
        disp = make_disp(atr="2.0")
        has, _, _ = g._check_delivery_fvg(sig, disp)
        assert has is False


# ── Grade assignment ──────────────────────────────────────────────────

class TestGradeAssignment:
    def test_grade_b_minus_when_momentum_weak(self):
        """body_to_atr < 1.0 → weak momentum (0pts), score too low → F, fails."""
        g = grader_with_htf()
        sig = make_signal()
        disp = make_disp(body_to_atr="0.8")
        grade = g.score(sig, disp, active_fvgs=[])
        assert grade.grade == "F"
        assert grade.passes is False
        assert grade.momentum_quality == "weak"

    def test_grade_b_when_no_clear_target(self):
        """No HTF label and target far from any swing or session extreme → B, fails.

        Setup: entry=2405 (short), target=2350.
          - HTF swings: highs=[2500] (above entry, irrelevant for short target check),
            lows=[2500] (above entry 2405, so no lows_below entry → no swing match).
          - Session range: (2415, 2380); session_low=2380; |2350 - 2380| = 30,
            ATR=2.0 so tol=6 → 30 > 6 → no session match.
        Result: no structural target → grade B.
        """
        g = SetupGrader()
        g.update_htf_swings(highs=[Decimal("2500")], lows=[Decimal("2500")])
        g._session_ranges["NY AM"] = (Decimal("2415"), Decimal("2380"))
        g._last_killzone = "NY AM"
        sig = make_signal(target="2350", rationale="NY AM: no structural target")
        disp = make_disp(body_to_atr="1.8")
        grade = g.score(sig, disp, active_fvgs=[])
        assert grade.grade == "F"
        assert grade.passes is False
        assert grade.target_clear is False

    def test_grade_b_when_same_side_fvg_overlap_no_htf_rescue(self):
        """Same-side overlapping FVG with no 30min containment → B, fails."""
        g = grader_with_htf()
        sig = make_signal(rationale="NY AM: HTF: 4h FVG @ 2395")
        disp = make_disp(body_to_atr="1.8")
        # Overlapping same-side (bearish for short signal's iFVG) FVG
        # Short signal → iFVG side = "bearish"
        overlapping = fvg("bearish", "2402", "2404")  # overlaps [2401,2403]
        grade = g.score(sig, disp, active_fvgs=[overlapping])
        assert grade.grade == "F"
        assert grade.passes is False
        assert grade.fvg_singular is False

    def test_opposite_side_overlap_is_bpr_not_fail(self):
        """Opposite-side FVG overlap = BPR → does NOT fail singularity (Correction 4)."""
        g = grader_with_htf()
        sig = make_signal(rationale="NY AM: HTF: 4h FVG @ 2395")
        disp = make_disp(body_to_atr="1.8")
        # Short signal → iFVG side = "bearish"; opposite side = "bullish"
        bpr_fvg = fvg("bullish", "2402", "2404")  # overlaps but opposite side
        grade = g.score(sig, disp, active_fvgs=[bpr_fvg])
        assert grade.fvg_singular is True
        assert grade.bpr_confluence is True
        assert grade.passes is True

    def test_htf_singularity_rescue_contained_in_30min_fvg(self):
        """Same-side stacked 1min FVGs that fit in one 30min FVG → singular on 30min (Correction 5)."""
        g = grader_with_htf()
        # 30min FVG [2399, 2405] contains both 1min FVGs [2401,2403] and [2402,2404]
        g._delivery_fvgs = [fvg("bearish", "2399", "2405")]
        sig = make_signal(rationale="NY AM: HTF: 4h FVG @ 2395")
        disp = make_disp(body_to_atr="1.8")
        stacked = fvg("bearish", "2402", "2404")  # same-side, overlaps
        grade = g.score(sig, disp, active_fvgs=[stacked])
        assert grade.fvg_singular is True
        assert grade.singularity_timeframe == "30min"
        assert grade.passes is True

    def test_grade_a_minus_when_wrong_premium_discount(self):
        """Entry not in correct P/D → pd=False (20pts lost); score 20 → D, passes."""
        g = SetupGrader()
        # Short entry 2395 below session mid 2402 → discount (wrong for short)
        g.update_htf_swings(highs=[Decimal("2420")], lows=[Decimal("2380")])
        g._session_ranges["NY AM"] = (Decimal("2415"), Decimal("2389"))
        g._last_killzone = "NY AM"
        sig = make_signal(entry="2395", stop="2398", target="2382",
                          rationale="NY AM: HTF: 30min swing @ 2382")
        disp = make_disp(body_to_atr="1.8")
        grade = g.score(sig, disp, active_fvgs=[])
        assert grade.grade == "D"
        assert grade.passes is True
        assert grade.premium_discount_ok is False

    def test_grade_a_when_correct_pd_and_strong_momentum(self):
        """Correct P/D + strong momentum, no fib/delivery/bpr → score 40 → C."""
        # Short: session [2390,2415] mid=2402.5; entry=2405 > mid → premium ✓
        # HTF swings: high=2420 above entry, low=2385 below → mid=2402.5; 2405 > 2402.5 ✓
        # fib_ext≈0.7 (<1.0→0pts), pd=True(20pts), delivery=False(0pts),
        # strong(15pts), bpr=False(0pts), target=True(5pts) → 40 → C
        g = grader_with_htf(sess_high="2415", sess_low="2390",
                             swing_highs=["2420"], swing_lows=["2385"])
        sig = make_signal(entry="2405", rationale="NY AM: HTF: 4h FVG @ 2395")
        disp = make_disp(body_to_atr="1.8", body_to_range="0.7")
        grade = g.score(sig, disp, active_fvgs=[])
        assert grade.grade == "C"
        assert grade.passes is True
        assert grade.premium_discount_ok is True

    def test_grade_a_plus_with_delivery_fvg(self):
        """Delivery FVG in P/D adds 20pts: score 60 → B."""
        # fib_ext≈0.7(0pts)+pd=True(20pts)+delivery+pd(20pts)+strong(15pts)+bpr=False(0pts)+target(5pts)=60
        g = grader_with_htf(sess_high="2415", sess_low="2390",
                             swing_highs=["2420"], swing_lows=["2385"])
        # Add bearish 30min delivery FVG at sweep (2408) in premium (mid=2402.5)
        g._delivery_fvgs = [fvg("bearish", "2407", "2410")]  # sweep=2408 inside
        sig = make_signal(entry="2405", sweep_extreme="2408",
                          rationale="NY AM: HTF: 4h FVG @ 2395")
        disp = make_disp(body_to_atr="1.8", body_to_range="0.7", atr="2.0")
        grade = g.score(sig, disp, active_fvgs=[])
        assert grade.grade == "B"
        assert grade.has_delivery_fvg is True

    def test_bpr_auto_a_plus_when_bpr_and_correct_pd(self):
        """BPR confluence + correct P/D + decent momentum: score 42 → C."""
        # fib_ext≈0.7(0pts)+pd=True(20pts)+delivery=False(0pts)+decent(7pts)+bpr(10pts)+target(5pts)=42
        g = grader_with_htf(sess_high="2415", sess_low="2390",
                             swing_highs=["2420"], swing_lows=["2385"])
        sig = make_signal(entry="2405", rationale="NY AM: HTF: 4h FVG @ 2395")
        disp = make_disp(body_to_atr="1.0")  # decent momentum (not strong)
        # Opposite-side FVG = BPR
        opp = fvg("bullish", "2402", "2404")
        grade = g.score(sig, disp, active_fvgs=[opp], bars_since_sweep=5)
        assert grade.grade == "C"
        assert grade.bpr_confluence is True

    def test_no_recent_sweep_caps_at_b_without_delivery(self):
        """bars_since_sweep > window AND no delivery FVG → passes=False (Rule A); score still computed → C."""
        g = grader_with_htf()
        sig = make_signal(rationale="NY AM: HTF: 4h FVG @ 2395")
        disp = make_disp(body_to_atr="1.8")
        grade = g.score(sig, disp, active_fvgs=[], bars_since_sweep=15, sweep_window_bars=10)
        assert grade.grade == "C"
        assert grade.passes is False
        assert grade.recent_sweep_ok is False

    def test_fib_displacement_ok_when_reversal_exceeds_multiplier(self):
        """Rule E: displacement body / sweep bar range >= min_mult → fib_displacement_ok=True."""
        g = grader_with_htf()
        # disp body = 1.8 × 2.0 = 3.6; sweep bar range 3.0 → extension 1.2
        sig = make_signal(rationale="NY AM: HTF: 4h FVG @ 2395", sweep_bar_range="3.0")
        disp = make_disp(body_to_atr="1.8", atr="2.0")
        grade = g.score(sig, disp, active_fvgs=[], min_displacement_mult=Decimal("1.0"))
        assert grade.fib_displacement_ok is True

    def test_fib_displacement_fail_when_under_multiplier(self):
        """Rule E: displacement body / sweep bar range < min_mult → fib_displacement_ok=False."""
        g = grader_with_htf()
        # disp body = 3.6; sweep bar range 6.0 → extension 0.6
        sig = make_signal(rationale="NY AM: HTF: 4h FVG @ 2395", sweep_bar_range="6.0")
        disp = make_disp(body_to_atr="1.8", atr="2.0")
        grade = g.score(sig, disp, active_fvgs=[], min_displacement_mult=Decimal("1.0"))
        assert grade.fib_displacement_ok is False

    def test_fib_can_exceed_one(self):
        """The old computation divided the displacement bar's body by its own
        range, capping fib at 1.0 forever — a fib>=1.0 gate was an off switch.
        A reversal body larger than the sweep bar's range must yield > 1.0x."""
        g = grader_with_htf()
        # disp body = 3.6; sweep (manipulation) bar range 2.0 → extension 1.8
        sig = make_signal(rationale="NY AM: HTF: 4h FVG @ 2395", sweep_bar_range="2.0")
        disp = make_disp(body_to_atr="1.8", atr="2.0")
        grade = g.score(sig, disp, active_fvgs=[], min_displacement_mult=Decimal("1.0"))
        assert grade.fib_extension == Decimal("1.8")
        assert grade.fib_displacement_ok is True

    def test_fib_fails_without_sweep_bar_range(self):
        """A signal with no sweep bar context (e.g. DEBUG force-signal) must
        not silently fall back to the broken displacement-bar proxy."""
        g = grader_with_htf()
        sig = make_signal(rationale="NY AM: HTF: 4h FVG @ 2395")  # sweep_bar_range=None
        disp = make_disp(body_to_atr="1.8", body_to_range="0.8", atr="2.0")
        grade = g.score(sig, disp, active_fvgs=[], min_displacement_mult=Decimal("0.7"))
        assert grade.fib_displacement_ok is False
        assert grade.fib_extension == Decimal("0")


class TestTargetClarityMode:
    """no-structural-target behavior is config-gated: reject (default) /
    penalty (downgrade one notch) / off (ignore)."""

    def test_penalty_mode_uses_score_threshold_not_letter_downgrade(self):
        """Penalty mode: missing target doesn't reject (step-2) — it lowers score and may affect passes."""
        disp = make_disp()
        # Baseline: clear target → target_clear=True, passes=True
        base = grader_with_htf().score(make_signal(target="2390"), disp, active_fvgs=[])
        assert base.target_clear is True
        assert base.passes is True

        # Penalty mode: target far from all structure → not hard-rejected at step 2
        g = grader_with_htf()
        g._target_clarity_mode = "penalty"
        pen = g.score(make_signal(target="2300"), disp, active_fvgs=[])
        assert pen.target_clear is False
        assert "no structural target" not in pen.reason    # not the step-2 hard reject path
        # Grade is NOT downgraded — it is score-derived (loses 5 target pts but letter unchanged)
        assert pen.grade == base.grade
        # passes reflects score >= 35 threshold (this setup scores 35 without target → still passes)
        assert pen.passes is True

    def test_off_mode_ignores_missing_target(self):
        g = grader_with_htf()
        g._target_clarity_mode = "off"
        off = g.score(make_signal(target="2300"), disp=make_disp(), active_fvgs=[])
        base = grader_with_htf().score(make_signal(target="2390"), make_disp(), active_fvgs=[])
        assert off.grade == base.grade                        # no penalty at all

    def test_reject_is_the_default(self):
        g = grader_with_htf()  # no mode set
        rej = g.score(make_signal(target="2300"), make_disp(), active_fvgs=[])
        assert rej.passes is False
        assert "no structural target" in rej.reason
