"""B44 — iFVG mid-session signal block by ET hour (ifvg_block_hours parameter).

Defining-behavior tests:
1. block_hours=[] (default): signals fire at any hour (existing behavior unchanged).
2. block_hours=[12]: displacement bar at 12:15 ET → on_displacement returns None.
3. block_hours=[12]: displacement bar at 11:59 ET → signal fires (11:xx not blocked).
4. block_hours=[12]: sweep state updated even during blocked hour (state not lost).
5. block_hours=[12]: bar at 13:00 ET → signal fires (13:xx not blocked).
6. CLI --set coercion: list[int] fields passed as comma strings coerce to ints
   (regression test — model_copy without validate stores str; in block check
   `11 in ["11"]` is False, so block would silently not fire).
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.sim.events import Bar
from app.strategy.composer import ComposerConfig, SweepDisplacementComposer
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.strategy.liquidity import Swing, SweepEvent

ET = ZoneInfo("America/New_York")

# Timestamps: 2026-01-06 (Tuesday) at various ET hours, in UTC (winter: UTC-5)
_12_15_ET = datetime(2026, 1, 6, 17, 15, tzinfo=timezone.utc)  # 12:15 ET
_11_59_ET = datetime(2026, 1, 6, 16, 59, tzinfo=timezone.utc)  # 11:59 ET
_13_00_ET = datetime(2026, 1, 6, 18, 0, tzinfo=timezone.utc)   # 13:00 ET
_10_00_ET = datetime(2026, 1, 6, 15, 0, tzinfo=timezone.utc)   # 10:00 ET (inside NY AM)


def _bar(ts: datetime) -> Bar:
    return Bar(
        instrument="MNQ", timeframe="5min", ts=ts,
        open=Decimal("100"), high=Decimal("102"),
        low=Decimal("97"), close=Decimal("101"),
        volume=500,
    )


def _disp(ts: datetime) -> DisplacementEvent:
    fvg = FairValueGap(side="bearish", low=Decimal("98.50"), high=Decimal("99.70"), created_at=ts)
    return DisplacementEvent(
        side="bullish",
        displacement_bar=_bar(ts),
        body_size=Decimal("1.0"),
        atr_at_event=Decimal("5.0"),
        body_to_atr=Decimal("0.2"),
        fvg=fvg,
    )


def _low_sweep(ts: datetime) -> SweepEvent:
    return SweepEvent(
        side="low",
        swept_swing=Swing(
            kind="low", price=Decimal("97.5"),
            bar_ts=ts - timedelta(minutes=5),
            confirmed_ts=ts - timedelta(minutes=2),
        ),
        pattern="B_one_bar",
        sweep_extreme=Decimal("97.0"),
        completed_at=ts,
        sweep_bar=_bar(ts),
    )


def _composer(block_hours: list[int]) -> SweepDisplacementComposer:
    cfg = ComposerConfig(
        instrument="MNQ",
        trend_ema_period=0,
        stop_buffer=Decimal("0.30"),
        block_hours=block_hours,
    )
    return SweepDisplacementComposer(cfg)


def _emit(composer: SweepDisplacementComposer, sweep_ts: datetime, disp_ts: datetime):
    """Arm sweep at sweep_ts; fire displacement at disp_ts."""
    composer.on_sweep(_bar(sweep_ts), _low_sweep(sweep_ts))
    return composer.on_displacement(_bar(disp_ts), _disp(disp_ts))


class TestIFVGBlockHours:

    def test_default_empty_fires_any_hour(self):
        """block_hours=[] (default): signals fire at any hour including noon."""
        c = _composer([])
        sig = _emit(c, _10_00_ET, _12_15_ET)
        assert sig is not None, "Should fire with no hour block"

    def test_blocked_hour_suppresses_signal(self):
        """block_hours=[12]: displacement at 12:15 ET suppressed."""
        assert _12_15_ET.astimezone(ET).hour == 12, "sanity: 17:15 UTC = 12:15 ET"
        c = _composer([12])
        sig = _emit(c, _10_00_ET, _12_15_ET)
        assert sig is None, "12:15 ET should be suppressed when block_hours=[12]"

    def test_adjacent_hour_not_blocked(self):
        """block_hours=[12]: 11:59 ET fires (11:xx not in block list)."""
        assert _11_59_ET.astimezone(ET).hour == 11, "sanity: 16:59 UTC = 11:59 ET"
        c = _composer([12])
        sig = _emit(c, _10_00_ET, _11_59_ET)
        assert sig is not None, "11:59 ET should fire (11:xx not blocked)"

    def test_sweep_state_preserved_during_blocked_hour(self):
        """Sweep armed at 10:00 ET; displacement at 12:15 ET blocked; displacement at 13:00 ET fires."""
        assert _13_00_ET.astimezone(ET).hour == 13, "sanity: 18:00 UTC = 13:00 ET"
        c = _composer([12])  # only block 12:xx
        # Arm sweep at 10:00 ET
        c.on_sweep(_bar(_10_00_ET), _low_sweep(_10_00_ET))
        assert len(c.awaiting) == 1, "Sweep should be armed"

        # Displacement at blocked hour — should return None but NOT clear sweep state
        result_blocked = c.on_displacement(_bar(_12_15_ET), _disp(_12_15_ET))
        assert result_blocked is None, "12:15 ET displacement should be suppressed"
        assert len(c.awaiting) == 1, "Sweep state must be preserved (not consumed) during blocked hour"

        # Displacement at 13:00 ET (not blocked) — should fire
        result_fires = c.on_displacement(_bar(_13_00_ET), _disp(_13_00_ET))
        assert result_fires is not None, "13:00 ET displacement should fire (only 12:xx blocked)"

    def test_second_unblocked_hour_fires(self):
        """block_hours=[12]: displacement at 13:00 ET fires (13:xx not blocked)."""
        c = _composer([12])
        sig = _emit(c, _10_00_ET, _13_00_ET)
        assert sig is not None, "13:00 ET should fire when only 12:xx is blocked"

    def test_cli_set_coercion_list_int(self):
        """--set ifvg_block_hours=11,12,13 must coerce elements to int, not store str.

        Regression: model_copy(update={...}) without validate stores list[str].
        The block check `et_hour in config.block_hours` is int-in-list[str] = False,
        so block silently fails. The fix in equity_export.py + run_monthly_combine.py
        uses get_args(field.annotation) to detect elem_type and coerce.
        """
        from typing import get_args
        from app.bot_config import StrategyParams

        strategy = StrategyParams()
        cur = getattr(strategy, "ifvg_block_hours")
        assert isinstance(cur, list)

        # Simulate the fixed --set handler
        field_ann = type(strategy).model_fields["ifvg_block_hours"].annotation
        elem_type = (get_args(field_ann) or (str,))[0]
        assert elem_type is int, f"Expected int elem type, got {elem_type}"

        v = "11,12,13"
        new_val = [elem_type(x.strip()) for x in v.split(",") if x.strip()]
        assert new_val == [11, 12, 13], f"Expected [11,12,13] int list, got {new_val!r}"

        strategy2 = strategy.model_copy(update={"ifvg_block_hours": new_val})
        assert strategy2.ifvg_block_hours == [11, 12, 13]
        assert all(isinstance(h, int) for h in strategy2.ifvg_block_hours), \
            "block_hours must be list[int]; str elements would silently disable the block"
