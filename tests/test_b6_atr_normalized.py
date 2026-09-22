"""
B6: ATR-normalized displacement and stop-buffer thresholds.

Defining-behavior tests. These MUST fail before implementation and pass after.

Economic motivation: fixed-point min_absolute_body=5.0 and stop_buffer=3.0
are ~2x stricter at NQ 12k (2022) than at NQ 21k (2024+). Percent-of-price
alternatives let the strategy treat equivalent moves equivalently across
price eras, potentially waking up 2021/2023 drought months.

Calibration constants (match 2024+ behavior at ~21k NQ by construction):
  min_absolute_body_pct = 5.0 / 21000 ≈ 0.000238
  stop_buffer_pct       = 3.0 / 21000 ≈ 0.000143
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.sim.events import Bar
from app.strategy.displacement import DisplacementConfig, DisplacementDetector
from app.strategy.composer import ComposerConfig
from app.bot_config import StrategyParams

BASE_TS = datetime(2021, 1, 1, 10, 0, tzinfo=timezone.utc)


def _bar(i: int, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(
        instrument="MNQ",
        timeframe="5min",
        ts=BASE_TS + timedelta(minutes=i),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=1000,
    )


def _fire_displacement(
    pct: Decimal,
    body: float,
    price: float,
    fixed_min: Decimal = Decimal("0"),
) -> bool:
    """
    True if a bullish displacement fires for a `body`-pt move at `price`.
    Short ATR period (3) for fast warmup. body_atr_multiple=1.0, so any
    body >= ATR (~1.0 from warmup bars) passes the ATR check — the only
    variable is the absolute-body threshold.
    """
    cfg = DisplacementConfig(
        atr_period=3,
        body_atr_multiple=Decimal("1.0"),
        min_body_to_range_ratio=Decimal("0.6"),
        min_absolute_body=fixed_min,
        min_absolute_body_pct=pct,
    )
    det = DisplacementDetector(cfg)
    # Warm up with 5 tiny bars (~1pt range → ATR ≈ 1.0).
    for i in range(5):
        det.on_bar(_bar(i, price, price + 0.5, price - 0.5, price))
    # b1: quiet bar
    det.on_bar(_bar(5, price, price + 0.5, price - 0.5, price))
    # b2: bullish displacement; body=`body`, range=body+1 → ratio≈0.75≥0.6.
    det.on_bar(_bar(6, price, price + body + 0.5, price - 0.5, price + body))
    # b3: close the 3-bar window and capture the result.
    c3 = price + body
    result = det.on_bar(_bar(7, c3, c3 + 0.5, c3 - 0.5, c3))
    return result is not None


class TestMinAbsoluteBodyPct:

    def test_pct_allows_small_body_at_low_price(self):
        """body=3.0 at price 12000: threshold = 12000*0.000238 = 2.856 < 3.0 → fires."""
        assert _fire_displacement(Decimal("0.000238"), body=3.0, price=12000.0)

    def test_pct_blocks_small_body_at_high_price(self):
        """body=3.0 at price 21000: threshold = 21000*0.000238 = 4.998 > 3.0 → blocked."""
        assert not _fire_displacement(Decimal("0.000238"), body=3.0, price=21000.0)

    def test_pct_zero_falls_back_to_fixed_threshold(self):
        """pct=0 disables pct; fixed min_absolute_body=5.0 blocks body=3.0 at any price."""
        assert not _fire_displacement(
            Decimal("0"), body=3.0, price=12000.0, fixed_min=Decimal("5.0")
        )

    def test_large_body_passes_pct_at_high_price(self):
        """body=5.1 at price 21000: threshold=4.998 < 5.1 → fires."""
        assert _fire_displacement(Decimal("0.000238"), body=5.1, price=21000.0)

    def test_threshold_boundary_just_below(self):
        """body=2.85 at price 12000: threshold=2.856 > 2.85 → blocked (just below)."""
        assert not _fire_displacement(Decimal("0.000238"), body=2.85, price=12000.0)


class TestStopBufferPct:

    def test_composer_config_accepts_stop_buffer_pct(self):
        """ComposerConfig stores stop_buffer_pct correctly."""
        cfg = ComposerConfig(
            instrument="MNQ",
            stop_buffer=Decimal("0"),
            stop_buffer_pct=Decimal("0.000143"),
        )
        assert cfg.stop_buffer_pct == Decimal("0.000143")

    def test_stop_buffer_pct_default_is_zero(self):
        """Default stop_buffer_pct is 0 (feature off unless explicitly set)."""
        cfg = ComposerConfig(instrument="MNQ")
        assert cfg.stop_buffer_pct == Decimal("0")


class TestStrategyParamsWiring:

    def test_strategy_params_has_min_absolute_body_pct(self):
        """StrategyParams exposes min_absolute_body_pct."""
        s = StrategyParams(min_absolute_body_pct=Decimal("0.000238"))
        assert s.min_absolute_body_pct == Decimal("0.000238")

    def test_strategy_params_has_stop_buffer_pct(self):
        """StrategyParams exposes stop_buffer_pct."""
        s = StrategyParams(stop_buffer_pct=Decimal("0.000143"))
        assert s.stop_buffer_pct == Decimal("0.000143")

    def test_both_pct_fields_default_to_zero(self):
        """Both pct fields default to 0 — feature is off by default."""
        s = StrategyParams()
        assert s.min_absolute_body_pct == Decimal("0")
        assert s.stop_buffer_pct == Decimal("0")
