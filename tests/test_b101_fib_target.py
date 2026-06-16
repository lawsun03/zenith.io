"""B101: Fibonacci-extension TARGET on the iFVG composer.

target = entry ± ext × |sweep_extreme − displacement_bar_extreme|, stop unchanged.
The ORB-side counterpart lives in test_orb.py::test_fib_target_ext_overrides_fixed_r.
"""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.broker.events import Bar
from app.strategy.composer import ComposerConfig, SweepDisplacementComposer
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.strategy.liquidity import SweepEvent, Swing

ET = ZoneInfo("America/New_York")


def bar(ts, o, h, l, c):
    return Bar(instrument="MNQ", timeframe="5min", ts=ts,
               open=Decimal(o), high=Decimal(h), low=Decimal(l),
               close=Decimal(c), volume=100)


def _long_signal(composer):
    ts0 = datetime(2026, 3, 4, 9, 30, tzinfo=ET)
    b_sweep = bar(ts0, "21000", "21005", "20985", "21002")
    sweep = SweepEvent(
        side="low",
        swept_swing=Swing(kind="low", price=Decimal("20992"), bar_ts=ts0, confirmed_ts=ts0),
        pattern="B_one_bar", sweep_extreme=Decimal("20985"),
        completed_at=ts0, sweep_bar=b_sweep,
    )
    composer.on_sweep(b_sweep, sweep)
    composer.on_bar_close(b_sweep)
    ts1 = datetime(2026, 3, 4, 9, 31, tzinfo=ET)
    b_disp = bar(ts1, "21002", "21030", "21001", "21028")   # disp high = 21030
    fvg = FairValueGap(side="bullish", low=Decimal("21005"),
                       high=Decimal("21020"), created_at=ts1)
    event = DisplacementEvent(side="bullish", displacement_bar=b_disp,
                              body_size=Decimal("26"), atr_at_event=Decimal("5"),
                              body_to_atr=Decimal("5.2"), fvg=fvg)
    return composer.on_displacement(b_disp, event)


def test_fib_target_overrides_fixed_r_and_keeps_stop():
    # leg = disp_high(21030) - sweep_extreme(20985) = 45; entry = fvg.high = 21020.
    base = _long_signal(SweepDisplacementComposer(
        ComposerConfig(instrument="MNQ", trend_ema_period=0)))
    fib = _long_signal(SweepDisplacementComposer(
        ComposerConfig(instrument="MNQ", trend_ema_period=0,
                       fib_target_ext=Decimal("1.618"))))
    assert base is not None and fib is not None
    assert fib.entry == base.entry          # entry unchanged
    assert fib.stop == base.stop            # stop unchanged (the spec invariant)
    assert fib.target != base.target        # target overridden
    # 21020 + 1.618 * 45 = 21092.81
    assert fib.target == Decimal("21092.81")
