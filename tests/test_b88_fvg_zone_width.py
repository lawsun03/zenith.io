"""
B88: FVG zone-width quality gate -- Phase 0 defining-behavior tests.

Tests:
1. Composer builds signal with fvg_zone_pts = fvg_high - fvg_low (Decimal) when FVG present.
2. Composer builds signal with fvg_zone_pts = None when no FVG (displacement-only mode).
3. equity_export --trade-csv includes fvg_zone_pts column; non-empty for iFVG trade with zone.
4. equity_export --trade-csv has empty fvg_zone_pts for ORB trade (no zone in dict).

WHY: Phase 1 buckets iFVG trades by FVG zone width (fvg_zone_pts quintile) to test whether
narrow zones predict better inversion quality. If the field is absent from the Signal or
missing from the CSV layer, the quintile analysis cannot run. If it is miscomputed
(e.g. using raw FVG edges instead of the zone width), the bucket assignment is corrupted.
Tests pin the computation at the composer level (zone_high - zone_low) and the CSV output
layer independently.
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.broker.events import Bar
from app.strategy.composer import ComposerConfig, SweepDisplacementComposer, _Awaiting
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.strategy.liquidity import Swing, SweepEvent

# ET 10:00 on a Monday — inside NY-AM killzone (14:00 UTC = 10:00 ET)
BASE_TS = datetime(2024, 3, 18, 14, 0, tzinfo=timezone.utc)


def _bar(ts: datetime, side: str = "bullish") -> Bar:
    if side == "bullish":
        return Bar(instrument="MNQ", timeframe="5min", ts=ts,
                   open=Decimal("21000"), high=Decimal("21020"),
                   low=Decimal("20990"), close=Decimal("21015"), volume=500)
    return Bar(instrument="MNQ", timeframe="5min", ts=ts,
               open=Decimal("21000"), high=Decimal("21010"),
               low=Decimal("20980"), close=Decimal("20985"), volume=500)


def _swing(price: float, ts: datetime) -> Swing:
    return Swing(kind="low", price=Decimal(str(price)), bar_ts=ts, confirmed_ts=ts)


def _sweep(side: str, ts: datetime) -> SweepEvent:
    b = Bar(instrument="MNQ", timeframe="5min", ts=ts,
            open=Decimal("21000"), high=Decimal("21010"),
            low=Decimal("20985"), close=Decimal("20995"), volume=300)
    swept_price = 20990.0 if side == "low" else 21005.0
    sweep_extreme = 20985.0 if side == "low" else 21010.0
    return SweepEvent(
        side=side,
        swept_swing=_swing(swept_price, ts),
        pattern="B_one_bar",
        sweep_extreme=Decimal(str(sweep_extreme)),
        completed_at=ts,
        sweep_bar=b,
    )


def _disp_event_with_fvg(disp_side: str, ts: datetime,
                          fvg_low: Decimal, fvg_high: Decimal) -> DisplacementEvent:
    b = _bar(ts, disp_side)
    fvg = FairValueGap(side=disp_side, low=fvg_low, high=fvg_high, created_at=ts)
    return DisplacementEvent(
        side=disp_side,
        displacement_bar=b,
        body_size=Decimal("10"),
        atr_at_event=Decimal("5"),
        body_to_atr=Decimal("2.0"),
        fvg=fvg,
    )


def _disp_event_no_fvg(disp_side: str, ts: datetime) -> DisplacementEvent:
    b = _bar(ts, disp_side)
    return DisplacementEvent(
        side=disp_side,
        displacement_bar=b,
        body_size=Decimal("10"),
        atr_at_event=Decimal("5"),
        body_to_atr=Decimal("2.0"),
        fvg=None,
    )


def _composer(confirmation: str = "ifvg") -> SweepDisplacementComposer:
    return SweepDisplacementComposer(ComposerConfig(
        instrument="MNQ",
        displacement_window_bars=10,
        stop_buffer=Decimal("1.0"),
        r_multiple=Decimal("2.5"),
        daily_bias_gate_enabled=False,
        confirmation=confirmation,
    ))


def _arm_and_fire_with_fvg(composer: SweepDisplacementComposer,
                            disp_side: str, ts: datetime,
                            fvg_low: Decimal, fvg_high: Decimal):
    sweep_side = "low" if disp_side == "bullish" else "high"
    composer._awaiting.append(_Awaiting(
        sweep=_sweep(sweep_side, ts - timedelta(minutes=5)),
        bars_since_sweep=0,
        killzone_name="NY-AM",
    ))
    bar = _bar(ts, disp_side)
    return composer.on_displacement(bar, _disp_event_with_fvg(disp_side, ts, fvg_low, fvg_high))


def _arm_and_fire_no_fvg(composer: SweepDisplacementComposer,
                          disp_side: str, ts: datetime):
    sweep_side = "low" if disp_side == "bullish" else "high"
    composer._awaiting.append(_Awaiting(
        sweep=_sweep(sweep_side, ts - timedelta(minutes=5)),
        bars_since_sweep=0,
        killzone_name="NY-AM",
    ))
    bar = _bar(ts, disp_side)
    return composer.on_displacement(bar, _disp_event_no_fvg(disp_side, ts))


# -- CSV helpers (same pattern as test_b79_freshness_deployed.py) ----------

def _fake_trade_ifvg(fvg_zone_pts: str | None) -> dict:
    t: dict = {
        "entry_ts": "2024-03-01T10:00:00",
        "exit_ts": "2024-03-01T14:00:00",
        "side": "long",
        "realized_pnl": Decimal("125.0"),
        "grade": "B",
        "criteria": {},
    }
    if fvg_zone_pts is not None:
        t["fvg_zone_pts"] = fvg_zone_pts
    return t


def _fake_trade_orb() -> dict:
    return {
        "entry_ts": "2024-03-01T09:35:00",
        "exit_ts": "2024-03-01T16:09:00",
        "side": "long",
        "realized_pnl": Decimal("-50.0"),
        # no grade → ORB; no fvg_zone_pts
    }


def _fake_result(trades: list[dict]) -> MagicMock:
    r = MagicMock()
    r.trades = trades
    r.stats.equity_curve = []
    r.stats.trades = len(trades)
    r.stats.net_pnl = Decimal("0")
    r.stats.profit_factor = Decimal("1")
    return r


# ---------------------------------------------------------------------------


class TestFvgZonePtsComposer:
    def test_fvg_zone_pts_is_zone_height_when_fvg_present(self):
        """Composer signal.fvg_zone_pts == fvg_high - fvg_low when FVG exists.

        WHY: Phase 1 buckets signals by FVG zone width. If the computed width
        is wrong (e.g. only fvg_high stored, not the difference), quintile
        bucket assignments are corrupted and the NO-GO decision is unreliable.
        """
        c = _composer("ifvg")
        fvg_low = Decimal("20995")
        fvg_high = Decimal("21000")
        sig = _arm_and_fire_with_fvg(c, "bullish", BASE_TS, fvg_low, fvg_high)
        assert sig is not None, "Signal should fire with matching sweep + displacement"
        assert sig.fvg_zone_pts == fvg_high - fvg_low, (
            f"fvg_zone_pts expected {fvg_high - fvg_low}, got {sig.fvg_zone_pts}"
        )

    def test_fvg_zone_pts_is_none_when_no_fvg(self):
        """Composer signal.fvg_zone_pts is None in displacement-only mode (no FVG).

        WHY: displacement-only signals have no FVG zone to measure. If fvg_zone_pts
        were set to 0 instead of None, these non-iFVG signals would pollute the
        narrow-width bucket with artificially small values and inflate narrow-FVG PF.
        """
        c = _composer("displacement_only")
        sig = _arm_and_fire_no_fvg(c, "bullish", BASE_TS)
        assert sig is not None, "displacement-only signal should fire"
        assert sig.fvg_zone_pts is None, (
            f"fvg_zone_pts must be None for displacement-only; got {sig.fvg_zone_pts}"
        )


class TestFvgZonePtsCsv:
    def test_fvg_zone_pts_column_present_for_ifvg_trade(self, tmp_path):
        """--trade-csv writes fvg_zone_pts column; non-empty for iFVG trade with FVG zone.

        WHY: Phase 1 reads fvg_zone_pts from the CSV to construct quintile buckets.
        If the column is absent or blank for iFVG trades, all trades land in the
        'missing' bucket and the quintile analysis silently produces no signal.
        """
        result = _fake_result([_fake_trade_ifvg("5.00")])
        out_csv = tmp_path / "eq.csv"
        trade_csv = tmp_path / "trades.csv"

        with patch("scripts.equity_export.load_bot_config"), \
             patch("scripts.equity_export.strategy_for"), \
             patch("scripts.equity_export.load_bars_csv", return_value=[]), \
             patch("scripts.equity_export.asyncio.run", return_value=result):
            sys.argv = [
                "equity_export.py", "--bars", "dummy.csv",
                "--out", str(out_csv), "--trade-csv", str(trade_csv),
            ]
            import scripts.equity_export as mod
            mod.main()

        rows = list(csv.DictReader(trade_csv.open()))
        assert "fvg_zone_pts" in rows[0], "fvg_zone_pts column must be present in CSV"
        assert rows[0]["fvg_zone_pts"] == "5.00", (
            f"fvg_zone_pts value must match trade dict; got {rows[0]['fvg_zone_pts']!r}"
        )

    def test_fvg_zone_pts_empty_for_orb_trade(self, tmp_path):
        """--trade-csv has empty fvg_zone_pts for ORB trade (no zone in trade dict).

        WHY: ORB signals have no FVG zone; the Phase 1 analysis filters to engine_type='ifvg'
        before using fvg_zone_pts. The column must still be present with an empty cell so
        csv.DictReader can parse rows uniformly without raising KeyError.
        """
        result = _fake_result([_fake_trade_orb()])
        out_csv = tmp_path / "eq.csv"
        trade_csv = tmp_path / "trades.csv"

        with patch("scripts.equity_export.load_bot_config"), \
             patch("scripts.equity_export.strategy_for"), \
             patch("scripts.equity_export.load_bars_csv", return_value=[]), \
             patch("scripts.equity_export.asyncio.run", return_value=result):
            sys.argv = [
                "equity_export.py", "--bars", "dummy.csv",
                "--out", str(out_csv), "--trade-csv", str(trade_csv),
            ]
            import scripts.equity_export as mod
            mod.main()

        rows = list(csv.DictReader(trade_csv.open()))
        assert "fvg_zone_pts" in rows[0], "fvg_zone_pts column must be present even for ORB"
        assert rows[0]["fvg_zone_pts"] == "", (
            "ORB trade must have empty fvg_zone_pts, not missing or erroring"
        )
