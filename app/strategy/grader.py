"""
Setup grader — scores an iFVG Signal against Dodgy's 5-criteria rating system.

Grades A+/A/A-/B/B-. Signals below A- are filtered before execution.

Five criteria:
  1. Momentum quality (from DisplacementEvent body_to_atr)
  2. Target clarity (HTF swing or session extreme alignment)
  3. FVG singularity (no same-side overlap; opposite-side = BPR bonus)
  4. Premium/discount positioning (session + HTF swing midpoints)
  5. Delivery from 30min FVG (directional, side + P/D required)

Spec corrections applied:
  - Correction 4: Opposite-side FVG overlap = BPR (positive), not singularity fail
  - Correction 5: Same-side stacked FVGs resolve as singular if they fit inside one 30min FVG
  - Correction 6: Delivery FVG requires correct side AND premium/discount alignment
  - Rule A: Recent sweep within N bars required (else cap at B without delivery FVG)
  - Rule C: CE-respected flag computed for journaling
  - Rule E: Fibonacci displacement quality (reversal range vs manipulation range)
  - Rule I: Gapping sack — 2+ consecutive same-side FVGs → fail unless 30min rescue
  - Rule J: BPR auto-A+ when BPR present + correct P/D + recent sweep
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, TYPE_CHECKING

from app.broker.events import Bar
from app.strategy.displacement import FairValueGap

if TYPE_CHECKING:
    from app.strategy.composer import Signal
    from app.strategy.displacement import DisplacementEvent

log = logging.getLogger(__name__)

MomentumQuality = Literal["strong", "decent", "weak"]


@dataclass(frozen=True)
class SetupGrade:
    """Result of grading one signal candidate against Dodgy's 5 criteria."""
    grade: Literal["A", "B", "C", "D", "F"]
    score: int                          # 0-100 weighted scorecard
    passes: bool                        # True if grade >= A-
    has_delivery_fvg: bool              # directional delivery (Correction 6)
    delivery_fvg_side: str | None       # "bullish" | "bearish" | None
    delivery_fvg_in_pd: bool            # delivery FVG in correct P/D zone
    premium_discount_ok: bool
    target_clear: bool
    fvg_singular: bool
    singularity_timeframe: str          # "1min" | "30min" | "none"
    momentum_quality: MomentumQuality
    bpr_confluence: bool                # opposite-side FVG overlap = BPR
    bpr_timeframe: str | None           # "1min" | None (5min/15min deferred)
    recent_sweep_ok: bool               # sweep within ifvg_sweep_window_bars
    fib_displacement_ok: bool           # reversal >= multiplier × manipulation
    fib_extension: Decimal              # measured multiple
    ce_respected: bool                  # price touched CE before continuing (Rule C)
    reason: str


class SetupGrader:
    """
    Stateful grader. Holds 30min delivery FVGs, HTF swings, and session ranges.
    Updated by the HTF refresh loop every 60s; scored once per signal candidate.
    """

    def __init__(self, target_clarity_mode: str = "reject") -> None:
        # How to treat a setup with no structural target: "reject" (cap B, the
        # original behavior), "penalty" (downgrade one notch), or "off" (ignore).
        self._target_clarity_mode = target_clarity_mode
        self._delivery_fvgs: list[FairValueGap] = []
        self._htf_swing_highs: list[Decimal] = []
        self._htf_swing_lows: list[Decimal] = []
        self._session_ranges: dict[str, tuple[Decimal, Decimal]] = {}
        self._last_killzone: str | None = None
        self.last_grade: SetupGrade | None = None  # cached for strategy_state SSE

    # ------------------------------------------------------------------
    # State update methods
    # ------------------------------------------------------------------

    def update_delivery_fvgs(self, bars_30min: list[Bar]) -> None:
        """Scan 30min bars for unmitigated FVGs to use as delivery context."""
        self._delivery_fvgs = self._compute_unmitigated_fvgs(bars_30min)
        log.debug("Grader: %d unmitigated 30min delivery FVGs", len(self._delivery_fvgs))

    def update_htf_swings(self, highs: list[Decimal], lows: list[Decimal]) -> None:
        self._htf_swing_highs = list(highs)
        self._htf_swing_lows = list(lows)

    def update_session_range(self, bar: Bar, killzone_name: str | None) -> None:
        """Expand session high/low for the active killzone; reset on killzone change."""
        if killzone_name is None:
            self._last_killzone = None
            return
        if killzone_name != self._last_killzone:
            self._session_ranges[killzone_name] = (bar.high, bar.low)
            self._last_killzone = killzone_name
        else:
            prev_high, prev_low = self._session_ranges.get(
                killzone_name, (bar.high, bar.low)
            )
            self._session_ranges[killzone_name] = (
                max(prev_high, bar.high),
                min(prev_low, bar.low),
            )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def session_range(self, killzone_name: str) -> tuple[Decimal, Decimal] | None:
        return self._session_ranges.get(killzone_name)

    def has_delivery_fvg(self, sweep_extreme: Decimal, atr: Decimal) -> bool:
        """Proximity-only check (used internally; score() uses directional version)."""
        tol = atr * Decimal("0.5")
        for fvg in self._delivery_fvgs:
            if fvg.low - tol <= sweep_extreme <= fvg.high + tol:
                return True
        return False

    # ------------------------------------------------------------------
    # Score
    # ------------------------------------------------------------------

    def score(
        self,
        signal: "Signal",
        disp: "DisplacementEvent",
        active_fvgs: list[FairValueGap],
        bars_since_sweep: int = 0,
        sweep_window_bars: int = 10,
        min_displacement_mult: Decimal = Decimal("1.0"),
    ) -> SetupGrade:
        """
        Score a signal against all 5 Dodgy criteria.
        Returns SetupGrade; check .passes to decide whether to trade.
        """
        b2 = disp.displacement_bar
        body = disp.body_size
        bar_range = b2.high - b2.low
        body_to_range = (body / bar_range) if bar_range > 0 else Decimal("0")

        # ── Step 1: Momentum ──────────────────────────────────────────
        if disp.body_to_atr < Decimal("1.0"):
            grade = self._make_grade(
                "B-", False,
                momentum_quality="weak",
                reason="momentum too weak (body_to_atr < 1.0) — B-",
                signal=signal, disp=disp,
                bars_since_sweep=bars_since_sweep,
                sweep_window_bars=sweep_window_bars,
                min_displacement_mult=min_displacement_mult,
            )
            self.last_grade = grade
            return grade

        # ── Step 2: Target clarity ─────────────────────────────────────
        # Config-gated: reject (cap B) / penalty (downgrade one notch) / off (ignore).
        target_clear = self._check_target_clarity(signal, disp.atr_at_event)
        target_penalty = False
        if not target_clear:
            if self._target_clarity_mode == "reject":
                grade = self._make_grade(
                    "B", False,
                    momentum_quality="decent",
                    target_clear=False,
                    reason="no structural target found — B",
                    signal=signal, disp=disp,
                    bars_since_sweep=bars_since_sweep,
                    sweep_window_bars=sweep_window_bars,
                    min_displacement_mult=min_displacement_mult,
                )
                self.last_grade = grade
                return grade
            elif self._target_clarity_mode == "penalty":
                target_penalty = True  # downgrade the final grade one notch
            # "off": fall through as if the target were clear (no penalty)

        # ── Step 3: FVG singularity (Corrections 4, 5, Rule I) ────────
        singular, sing_tf, bpr, bpr_tf = self._check_fvg_singular(
            signal, active_fvgs
        )
        if not singular:
            grade = self._make_grade(
                "B", False,
                momentum_quality="decent",
                target_clear=True,
                fvg_singular=False,
                singularity_timeframe="none",
                bpr_confluence=bpr,
                bpr_timeframe=bpr_tf,
                reason="overlapping same-side FVGs (gapping sack) — B",
                signal=signal, disp=disp,
                bars_since_sweep=bars_since_sweep,
                sweep_window_bars=sweep_window_bars,
                min_displacement_mult=min_displacement_mult,
            )
            self.last_grade = grade
            return grade

        # ── At least A- ───────────────────────────────────────────────
        momentum_quality: MomentumQuality = (
            "strong"
            if disp.body_to_atr >= Decimal("1.5") and body_to_range >= Decimal("0.6")
            else "decent"
        )

        # Rule A: sweep recency
        recent_sweep_ok = bars_since_sweep <= sweep_window_bars

        # Directional delivery FVG (Correction 6)
        delivery, delivery_side, delivery_in_pd = self._check_delivery_fvg(signal, disp)

        # Premium/discount (both session + HTF swing ranges must agree)
        pd_ok = self._check_premium_discount(signal)

        # Rule E: Fibonacci displacement quality
        fib_ok, fib_ext = self._check_fib_displacement(signal, disp, min_displacement_mult)

        # Rule C: CE-respected (only meaningful for retrace_ce mode)
        ce_respected = False  # set by external call if price touched CE

        # Grade assignment
        if not recent_sweep_ok and not delivery:
            # Rule A: no sweep and no delivery → cap at B
            grade_str: Literal["A+", "A", "A-", "B", "B-"] = "B"
            passes = False
        else:
            grade_str = "A-"
            if pd_ok and momentum_quality == "strong":
                grade_str = "A"
                if delivery:
                    grade_str = "A+"
            # Rule J: BPR auto-A+ when BPR + correct P/D + recent sweep
            if bpr and pd_ok and recent_sweep_ok:
                grade_str = "A+"
            passes = True

        # penalty mode: no structural target → downgrade one notch (A+→A, A→A-,
        # A-→B which then fails). Strong setups still trade; marginal ones don't.
        if target_penalty:
            _DOWN = {"A+": "A", "A": "A-", "A-": "B", "B": "B-", "B-": "B-"}
            grade_str = _DOWN[grade_str]
            passes = grade_str in ("A+", "A", "A-")

        reason = (
            f"{signal.killzone}: grade {grade_str} — "
            f"momentum={momentum_quality}, P/D={'ok' if pd_ok else 'off'}, "
            f"delivery={'yes' if delivery else 'no'}, "
            f"BPR={'yes' if bpr else 'no'}, "
            f"fib={'ok' if fib_ok else 'low'} ({fib_ext:.2f}x)"
            + (" [no-struct-target penalty]" if target_penalty else "")
        )
        log.info(reason)

        grade = SetupGrade(
            grade=grade_str,
            score=0,
            passes=passes,
            has_delivery_fvg=delivery,
            delivery_fvg_side=delivery_side,
            delivery_fvg_in_pd=delivery_in_pd,
            premium_discount_ok=pd_ok,
            target_clear=target_clear,
            fvg_singular=singular,
            singularity_timeframe=sing_tf,
            momentum_quality=momentum_quality,
            bpr_confluence=bpr,
            bpr_timeframe=bpr_tf,
            recent_sweep_ok=recent_sweep_ok,
            fib_displacement_ok=fib_ok,
            fib_extension=fib_ext,
            ce_respected=ce_respected,
            reason=reason,
        )
        self.last_grade = grade
        return grade

    # ------------------------------------------------------------------
    # Internal: criterion checks
    # ------------------------------------------------------------------

    def _check_target_clarity(self, signal: "Signal", atr: Decimal) -> bool:
        """True if target aligns with an HTF level, session extreme, or swing."""
        if "HTF:" in signal.rationale:
            return True
        tol = atr * Decimal("3")
        if signal.side == "long":
            return (
                any(abs(signal.target - h) <= tol
                    for h in self._htf_swing_highs if h > signal.entry)
                or any(abs(signal.target - high) <= tol
                       for (high, _) in self._session_ranges.values())
            )
        else:
            return (
                any(abs(signal.target - l) <= tol
                    for l in self._htf_swing_lows if l < signal.entry)
                or any(abs(signal.target - low) <= tol
                       for (_, low) in self._session_ranges.values())
            )

    def _check_fvg_singular(
        self,
        signal: "Signal",
        active_fvgs: list[FairValueGap],
    ) -> tuple[bool, str, bool, str | None]:
        """
        Returns (singular, timeframe, bpr_confluence, bpr_timeframe).

        Correction 4: Opposite-side overlap = BPR (positive).
        Correction 5: Same-side stacked FVGs may resolve as singular on 30min.
        Rule I: 2+ consecutive same-side FVGs = gapping sack → fail unless 30min rescue.
        """
        if signal.fvg_low is None or signal.fvg_high is None:
            return True, "1min", False, None

        same_side_overlaps: list[FairValueGap] = []
        opp_side_overlap = False

        # Determine the iFVG's side from signal direction
        # Long signal → bullish iFVG (was bearish FVG, now bullish)
        ifvg_side = "bullish" if signal.side == "long" else "bearish"

        for fvg in active_fvgs:
            # Skip if this IS the signal's own iFVG zone
            if fvg.low == signal.fvg_low and fvg.high == signal.fvg_high:
                continue
            # Check for overlap
            if fvg.low < signal.fvg_high and fvg.high > signal.fvg_low:
                if fvg.side == ifvg_side:
                    same_side_overlaps.append(fvg)
                else:
                    opp_side_overlap = True

        bpr = opp_side_overlap
        bpr_tf = "1min" if bpr else None

        if not same_side_overlaps:
            return True, "1min", bpr, bpr_tf

        # Rule I: gapping sack — 2+ consecutive same-side FVGs
        # Correction 5: try to rescue via 30min containment
        cluster = same_side_overlaps + [
            FairValueGap(
                side=ifvg_side,
                low=signal.fvg_low,
                high=signal.fvg_high,
                created_at=datetime.now(),
            )
        ]
        if self._htf_singularity_rescue(cluster):
            return True, "30min", bpr, bpr_tf

        return False, "none", bpr, bpr_tf

    def _htf_singularity_rescue(self, cluster: list[FairValueGap]) -> bool:
        """
        Correction 5: True if all FVGs in the cluster are contained within
        a single unmitigated 30min FVG.
        """
        if not self._delivery_fvgs:
            return False
        cluster_low = min(f.low for f in cluster)
        cluster_high = max(f.high for f in cluster)
        for fvg_30 in self._delivery_fvgs:
            if fvg_30.low <= cluster_low and fvg_30.high >= cluster_high:
                return True
        return False

    def _check_delivery_fvg(
        self, signal: "Signal", disp: "DisplacementEvent"
    ) -> tuple[bool, str | None, bool]:
        """
        Correction 6: Directional delivery FVG check.
        Returns (has_delivery, delivery_side, delivery_in_pd).

        Long (swept LOW): look for a BULLISH 30min FVG that the swept swing sat at,
                          and that FVG must be in discount.
        Short (swept HIGH): look for a BEARISH 30min FVG at the swept swing,
                            and that FVG must be in premium.
        """
        tol = disp.atr_at_event * Decimal("0.5")
        sweep = signal.sweep_extreme

        # Determine what side of delivery FVG we need
        target_fvg_side = "bullish" if signal.side == "long" else "bearish"

        for fvg in self._delivery_fvgs:
            if fvg.side != target_fvg_side:
                continue
            if fvg.low - tol <= sweep <= fvg.high + tol:
                # Check P/D: the delivery FVG must be in the right zone
                in_pd = self._delivery_fvg_in_pd(fvg, signal)
                return True, fvg.side, in_pd

        return False, None, False

    def _delivery_fvg_in_pd(self, fvg: FairValueGap, signal: "Signal") -> bool:
        """
        Long delivery: bullish FVG should be in discount (below session mid).
        Short delivery: bearish FVG should be in premium (above session mid).
        """
        sr = self._session_ranges.get(signal.killzone)
        if sr is None:
            return True  # no session data; can't determine — assume ok
        sess_mid = (sr[0] + sr[1]) / 2
        fvg_mid = (fvg.low + fvg.high) / 2
        if signal.side == "long":
            return fvg_mid < sess_mid  # discount
        else:
            return fvg_mid > sess_mid  # premium

    def _check_premium_discount(self, signal: "Signal") -> bool:
        """
        True if entry is in correct half of BOTH session range and HTF swing range.
        Long: entry < both midpoints (discount). Short: entry > both (premium).
        """
        sr = self._session_ranges.get(signal.killzone)
        if sr is None:
            return False
        sess_mid = (sr[0] + sr[1]) / 2

        highs_above = [h for h in self._htf_swing_highs if h > signal.entry]
        lows_below = [l for l in self._htf_swing_lows if l < signal.entry]
        if not highs_above or not lows_below:
            return False
        htf_mid = (min(highs_above) + max(lows_below)) / 2

        if signal.side == "long":
            return signal.entry < sess_mid and signal.entry < htf_mid
        else:
            return signal.entry > sess_mid and signal.entry > htf_mid

    def _check_fib_displacement(
        self,
        signal: "Signal",
        disp: "DisplacementEvent",
        min_mult: Decimal,
    ) -> tuple[bool, Decimal]:
        """
        Rule E: Fibonacci displacement quality.
        Manipulation leg = sweep bar body range (b2 = displacement_bar approximation).
        Reversal leg = displacement bar body.
        Extension = reversal_body / manipulation_body.
        Returns (ok, extension_multiple).
        """
        b2 = disp.displacement_bar
        manip_range = b2.high - b2.low  # sweep bar range as proxy
        if manip_range == 0:
            return False, Decimal("0")
        extension = disp.body_size / manip_range
        ok = extension >= min_mult
        return ok, extension

    def _compute_score(
        self,
        momentum_quality: MomentumQuality,
        pd_ok: bool,
        delivery: bool,
        delivery_in_pd: bool,
        bpr: bool,
        target_clear: bool,
        fib_ext: Decimal,
    ) -> int:
        """Weighted scorecard 0-100. Independent of passes logic."""
        score = 0
        # Fib extension (0-30)
        if fib_ext >= Decimal("1.5"):
            score += 30
        elif fib_ext >= Decimal("1.0"):
            score += 15
        # P/D (0-20)
        if pd_ok:
            score += 20
        # Delivery FVG (0-20): correct side + in P/D = 20, correct side only = 10
        if delivery:
            score += 20 if delivery_in_pd else 10
        # Momentum (0-15)
        if momentum_quality == "strong":
            score += 15
        elif momentum_quality == "decent":
            score += 7
        # BPR (0-10)
        if bpr:
            score += 10
        # Target clarity (0-5)
        if target_clear:
            score += 5
        return score

    @staticmethod
    def _score_to_grade(score: int) -> Literal["A", "B", "C", "D", "F"]:
        if score >= 75:
            return "A"
        if score >= 55:
            return "B"
        if score >= 35:
            return "C"
        if score >= 15:
            return "D"
        return "F"

    def _make_grade(
        self,
        grade: Literal["A+", "A", "A-", "B", "B-"],
        passes: bool,
        momentum_quality: MomentumQuality = "decent",
        target_clear: bool = True,
        fvg_singular: bool = True,
        singularity_timeframe: str = "1min",
        bpr_confluence: bool = False,
        bpr_timeframe: str | None = None,
        reason: str = "",
        signal: "Signal | None" = None,
        disp: "DisplacementEvent | None" = None,
        bars_since_sweep: int = 0,
        sweep_window_bars: int = 10,
        min_displacement_mult: Decimal = Decimal("1.0"),
    ) -> SetupGrade:
        """Helper for early-exit grade construction with safe defaults."""
        recent_sweep_ok = bars_since_sweep <= sweep_window_bars
        fib_ok = False
        fib_ext = Decimal("0")
        if disp is not None and signal is not None:
            fib_ok, fib_ext = self._check_fib_displacement(
                signal, disp, min_displacement_mult
            )
        return SetupGrade(
            grade=grade, score=0,
            passes=passes,
            has_delivery_fvg=False,
            delivery_fvg_side=None,
            delivery_fvg_in_pd=False,
            premium_discount_ok=False,
            target_clear=target_clear,
            fvg_singular=fvg_singular,
            singularity_timeframe=singularity_timeframe,
            momentum_quality=momentum_quality,
            bpr_confluence=bpr_confluence,
            bpr_timeframe=bpr_timeframe,
            recent_sweep_ok=recent_sweep_ok,
            fib_displacement_ok=fib_ok,
            fib_extension=fib_ext,
            ce_respected=False,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Internal: 30min FVG scanning
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_unmitigated_fvgs(bars: list[Bar]) -> list[FairValueGap]:
        """Find unmitigated 3-bar FVGs in a bar list."""
        gaps: list[tuple[int, FairValueGap]] = []
        for i in range(2, len(bars)):
            b1, b3 = bars[i - 2], bars[i]
            if b3.low > b1.high:
                gaps.append((i, FairValueGap(
                    side="bullish", low=b1.high, high=b3.low, created_at=b3.ts,
                )))
            elif b3.high < b1.low:
                gaps.append((i, FairValueGap(
                    side="bearish", low=b3.high, high=b1.low, created_at=b3.ts,
                )))
        out: list[FairValueGap] = []
        for formed_idx, fvg in gaps:
            mitigated = False
            for later in bars[formed_idx + 1:]:
                if fvg.side == "bullish" and later.low <= fvg.low:
                    mitigated = True
                    break
                if fvg.side == "bearish" and later.high >= fvg.high:
                    mitigated = True
                    break
            if not mitigated:
                out.append(fvg)
        return out
