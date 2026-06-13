"""
Backtest runner — reusable core used by scripts/backtest.py and the walk-forward optimizer.
"""
from __future__ import annotations

import dataclasses
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

from app.bot_config import StrategyParams
from app.broker.events import Bar, Fill
from app.broker.paper import PaperBroker
from app.execution.engine import ExecutionEngine, OrderOutcome, StrategyRunner
from app.strategy.grader import SetupGrader
from app.strategy.htf import HTFBiasTracker, HTFLevelFinder
from app.risk.config import fifty_k_combine, TopstepAccountConfig
from app.risk.state import RiskState
from app.strategy.composer import ComposerConfig, Signal, SweepDisplacementComposer
from app.strategy.displacement import DisplacementConfig, DisplacementDetector
from app.strategy.killzone import default_killzones, killzones_from_names
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker
from app.strategy.volume_profile import VolumeProfileTracker

# How the faithful backtest replicates the live HTF refresh (main._refresh_htf_once),
# which is what feeds the grader's structural-target gate. Without it every candidate
# fails grading ("no structural target found") and 0 trades are produced.
_HTF_REFRESH_BARS = 60     # rebuild once per ~hour of replay (HTF structure barely moves intraday)
_HTF_WINDOW_BARS = 12000   # trailing 1min bars to aggregate (bounds cost; ~enough for 4h swings)
_HTF_MIN_BARS = 480        # need several aggregated HTF bars before swings can confirm


_CT = ZoneInfo("America/Chicago")

from app.risk.flatten import trading_day_ct  # noqa: E402 — after _CT for clarity
# Thin alias so existing callers (tests/test_backtest_rollover.py, scripts/) keep working.
_trading_day_ct = trading_day_ct


def _refresh_backtest_htf(bars, s: StrategyParams, level_finder, bias_tracker, graders) -> None:
    """Rebuild HTF trackers + feed each grader from the bars seen so far.

    Mirrors main._refresh_htf_once but aggregates the replay stream (the
    PaperBroker has no get_historical_bars) using only past bars — no lookahead.

    Delivery FVGs are always refreshed: the 30min FVG check (criterion 5)
    is independent of whether HTF target selection is enabled.  Only swing
    levels (for target clarity / premium-discount) require level_finder."""
    from app.main import _aggregate_bars, _tf_to_seconds  # lazy: avoid import cycle
    bias_bars = _aggregate_bars(bars, _tf_to_seconds(s.htf_bias_timeframe), s.htf_bias_timeframe)
    swing_bars = _aggregate_bars(bars, _tf_to_seconds(s.htf_swing_timeframe), s.htf_swing_timeframe)
    # Always feed delivery FVGs — criterion 5 doesn't require HTF target gate.
    for g in graders:
        g.update_delivery_fvgs(swing_bars)
    if level_finder is not None:
        level_finder.rebuild(fvg_bars=bias_bars, swing_bars=swing_bars)
        for g in graders:
            g.update_htf_swings(level_finder.swing_highs, level_finder.swing_lows)
    if bias_tracker is not None:
        bias_tracker.rebuild(bias_bars)


@dataclass
class BacktestStats:
    trades: int
    wins: int
    losses: int
    win_rate: float
    net_pnl: Decimal
    gross_win: Decimal
    gross_loss: Decimal
    avg_win: Decimal
    avg_loss: Decimal
    profit_factor: float | None
    max_drawdown: Decimal
    expectancy: Decimal
    is_profitable: bool
    passed_combine: bool
    mll_breached: bool
    equity_curve: list[tuple[datetime, Decimal]]
    by_killzone: dict[str, dict]
    by_side: dict[str, dict] = field(default_factory=dict)


@dataclass
class BacktestConfig:
    instrument: str
    bars: Iterator[Bar]
    # Optional: only used on the legacy (no-VP) path. On the faithful path
    # (strategy_params set) the composer is rebuilt from StrategyParams and this
    # placeholder is ignored.
    composer_config: ComposerConfig = field(
        default_factory=lambda: ComposerConfig(instrument="MGC")
    )
    starting_balance: Decimal = field(default_factory=lambda: Decimal("50000"))
    soft_buffer: Decimal = field(default_factory=lambda: Decimal("500"))
    liquidity_config: LiquidityConfig = field(default_factory=LiquidityConfig)
    displacement_config: DisplacementConfig = field(default_factory=DisplacementConfig)
    enabled_killzones: list[str] | None = None
    timeframe: str = "1min"
    contracts: int = 1
    risk_per_trade_pct: Decimal = field(default_factory=lambda: Decimal("0"))  # 0 = fixed contracts
    slippage_ticks_market: int = 1
    commission_per_side: Decimal = field(default_factory=lambda: Decimal("0.74"))
    partial_profit_r: Decimal = field(default_factory=lambda: Decimal("0"))  # 0 = disabled
    max_entry_slippage_frac: Decimal = field(default_factory=lambda: Decimal("0"))  # 0 = disabled; refuse entries > frac × stop distance from market
    trail_1r: bool = False  # ablation T4: trailing 1R-ratchet exit, no TP, no partials
    # When set, the runner is built faithfully from this live StrategyParams —
    # including the VolumeProfileTracker and the VP gate (vp_enabled, target
    # override). This is the only way the backtest matches live behavior. When
    # None, the legacy sub-config path is used (no VP) for back-compat with
    # older tests/scripts. Sweeps target this object via the "strategy" container.
    strategy_params: StrategyParams | None = None
    label: str = ""
    enforce_risk_limits: bool = True  # False = disable MLL/DLL/DPL (backtest exploration only)


@dataclass
class BacktestResult:
    config: BacktestConfig
    stats: BacktestStats
    trades: list[dict]
    bars_processed: int
    rejected_signals: int
    label: str
    signals: list[dict] = field(default_factory=list)  # placed signals (parity diffing)


@dataclass
class SweepDimension:
    target: str       # parameter name
    values: list[Any]
    container: str    # "composer" | "liquidity" | "displacement"


def _build_runner(cfg: BacktestConfig) -> StrategyRunner:
    zones = (
        killzones_from_names(cfg.enabled_killzones)
        if cfg.enabled_killzones
        else default_killzones()
    )

    # Faithful path: derive every sub-config from the live StrategyParams,
    # mirroring main._build_runner exactly, and attach a VolumeProfileTracker.
    # This keeps r_multiple/stop_buffer/etc. single-sourced — VP.apply() reads
    # r_multiple from the same StrategyParams the composer was built from.
    if cfg.strategy_params is not None:
        s = cfg.strategy_params
        if s.engine == "combined":
            from app.strategy.combined import CombinedRunner
            primary = _build_runner(dataclasses.replace(
                cfg, strategy_params=s.model_copy(update={"engine": "ifvg"})))
            secondary = _build_runner(dataclasses.replace(
                cfg, strategy_params=s.model_copy(update={"engine": "orb"})))
            return CombinedRunner(primary=primary, secondary=secondary)
        if s.engine == "regime_switch":
            from app.strategy.regime_switch import RegimeSwitchRunner
            ifvg = _build_runner(dataclasses.replace(
                cfg, strategy_params=s.model_copy(update={"engine": "ifvg"})))
            orb = _build_runner(dataclasses.replace(
                cfg, strategy_params=s.model_copy(update={"engine": "orb"})))
            if s.rs_invert:
                return RegimeSwitchRunner(quiet=ifvg, active=orb, delegate=ifvg)
            return RegimeSwitchRunner(quiet=orb, active=ifvg, delegate=ifvg)
        if s.engine == "chop_breakout":
            from app.strategy.chop_breakout import (ChopBreakoutConfig,
                                                    ChopBreakoutDetector,
                                                    ChopBreakoutRunner)
            return ChopBreakoutRunner(
                instrument=cfg.instrument,
                timeframe=cfg.timeframe,
                detector=ChopBreakoutDetector(ChopBreakoutConfig(
                    instrument=cfg.instrument,
                    regime_metric=s.cb_regime_metric,
                    compression_lookback=s.cb_compression_lookback,
                    compression_percentile=s.cb_compression_percentile,
                    history_window=s.cb_history_window,
                    min_chop_bars=s.cb_min_chop_bars,
                    vwap_cross_min=s.cb_vwap_cross_min,
                    entry_mode=s.cb_entry_mode,
                    target_floor_r=s.cb_target_floor_r,
                    failed_breakout_bars=s.cb_failed_breakout_bars,
                    vwap_invalidation=s.cb_vwap_invalidation,
                    sma21_trail=s.cb_sma21_trail,
                    atr_period=s.atr_period,
                    body_atr_multiple=s.body_atr_multiple,
                    min_body_to_range_ratio=s.min_body_to_range_ratio,
                    min_absolute_body=s.min_absolute_body,
                    swing_lookback=s.swing_lookback,
                )),
                strategy_cfg=s,
            )
        if s.engine == "sweep_bos":
            from app.strategy.sweep_bos import (SweepBOSConfig, SweepBOSDetector,
                                                SweepBOSRunner)
            return SweepBOSRunner(
                instrument=cfg.instrument,
                timeframe=cfg.timeframe,
                detector=SweepBOSDetector(SweepBOSConfig(
                    instrument=cfg.instrument,
                    swing_lookback=s.swing_lookback,
                    min_penetration=s.min_penetration,
                    multi_bar_window=s.multi_bar_window,
                    stop_buffer=s.stop_buffer,
                    r_multiple=s.r_multiple,
                    bos_window_bars=s.ifvg_sweep_window_bars,
                    killzones=zones,
                )),
                strategy_cfg=s,
            )
        if s.engine == "vwap":
            from app.strategy.vwap import VWAPConfig, VWAPDetector, VWAPRunner
            return VWAPRunner(
                instrument=cfg.instrument,
                timeframe=cfg.timeframe,
                detector=VWAPDetector(VWAPConfig(
                    instrument=cfg.instrument,
                    anchor_et=s.vwap_anchor_et,
                    band_sigma=s.vwap_band_sigma,
                    stop_sigma=s.vwap_stop_sigma,
                )),
                strategy_cfg=s,
            )
        if s.engine == "orb":
            from app.strategy.orb import ORBComposer, ORBConfig, ORBDetector, ORBRunner
            _det = ORBDetector(ORBConfig(
                instrument=cfg.instrument,
                open_et=s.orb_open_et,
                range_minutes=s.orb_range_minutes,
                r_multiple=s.orb_r_multiple,
                max_trades_per_day=s.orb_max_trades_per_day,
                pdr_enabled=s.orb_pdr_enabled,
                reentry_after_stop=s.orb_reentry_after_stop,
                long_only=s.orb_long_only,
            ))
            return ORBRunner(
                instrument=cfg.instrument,
                timeframe=cfg.timeframe,
                detector=_det,
                strategy_cfg=s,
                composer=ORBComposer(
                    detector=_det,
                    reentry_after_stop=s.orb_reentry_after_stop,
                ),
            )
        if s.engine == "kz_levels":
            from app.strategy.kz_levels import KillzoneLevelTracker, KZLevelsRunner
            # kz_levels requires named session zones (London, NY AM, NY PM).
            # enabled_killzones="all" maps to all_day() which never closes and
            # would prevent _kz_ranges from ever being populated.
            kz_zones = default_killzones()
            return KZLevelsRunner(
                instrument=cfg.instrument,
                timeframe=cfg.timeframe,
                kz_tracker=KillzoneLevelTracker(),
                displacement=DisplacementDetector(DisplacementConfig(
                    atr_period=s.atr_period,
                    body_atr_multiple=s.body_atr_multiple,
                    min_body_to_range_ratio=s.min_body_to_range_ratio,
                    min_absolute_body=s.min_absolute_body,
                    atr_ref_lag_bars=s.atr_ref_lag_bars,
                    min_absolute_body_pct=s.min_absolute_body_pct,
                )),
                composer=SweepDisplacementComposer(ComposerConfig(
                    instrument=cfg.instrument,
                    displacement_window_bars=s.displacement_window_bars,
                    stop_buffer=s.stop_buffer,
                    stop_buffer_pct=s.stop_buffer_pct,
                    r_multiple=s.r_multiple,
                    killzones=kz_zones,
                    trend_ema_period=s.trend_ema_period,
                    cooldown_bars_after_stop=s.cooldown_bars_after_stop,
                    min_atr_filter=s.min_atr_filter,
                    max_atr_filter=s.max_atr_filter,
                    swing_stop_lookback=s.swing_stop_lookback,
                    allowed_sides=s.allowed_sides,
                    confirmation=s.confirmation,
                    max_stop_atr=s.max_stop_atr,
                    inversion_min_body_r=s.inversion_min_body_r,
                    daily_signal_cap=s.ifvg_daily_signal_cap,
                )),
                grader=SetupGrader(target_clarity_mode=s.target_clarity_mode),
                strategy_cfg=s,
                zones=kz_zones,
            )
        return StrategyRunner(
            instrument=cfg.instrument,
            timeframe=cfg.timeframe,
            liquidity=LiquidityTracker(LiquidityConfig(
                swing_lookback=s.swing_lookback,
                min_penetration=s.min_penetration,
                multi_bar_window=s.multi_bar_window,
                max_swings=50,
                min_penetration_atr_factor=(
                    s.min_penetration_atr_factor if s.min_penetration_atr_factor > 0 else None
                ),
            )),
            displacement=DisplacementDetector(DisplacementConfig(
                atr_period=s.atr_period,
                body_atr_multiple=s.body_atr_multiple,
                min_body_to_range_ratio=s.min_body_to_range_ratio,
                min_absolute_body=s.min_absolute_body,
                atr_ref_lag_bars=s.atr_ref_lag_bars,
                min_absolute_body_pct=s.min_absolute_body_pct,
            )),
            composer=SweepDisplacementComposer(ComposerConfig(
                instrument=cfg.instrument,
                displacement_window_bars=s.displacement_window_bars,
                stop_buffer=s.stop_buffer,
                stop_buffer_pct=s.stop_buffer_pct,
                r_multiple=s.r_multiple,
                killzones=zones,
                trend_ema_period=s.trend_ema_period,
                cooldown_bars_after_stop=s.cooldown_bars_after_stop,
                min_atr_filter=s.min_atr_filter,
                max_atr_filter=s.max_atr_filter,
                swing_stop_lookback=s.swing_stop_lookback,
                allowed_sides=s.allowed_sides,
                confirmation=s.confirmation,
                max_stop_atr=s.max_stop_atr,
                inversion_min_body_r=s.inversion_min_body_r,
                daily_signal_cap=s.ifvg_daily_signal_cap,
            )),
            grader=SetupGrader(target_clarity_mode=s.target_clarity_mode),
            strategy_cfg=s,
            vp=VolumeProfileTracker(),
        )

    # Legacy path (no VP): explicit sub-configs. Kept for back-compat.
    composer_cfg = dataclasses.replace(cfg.composer_config, killzones=zones)
    return StrategyRunner(
        instrument=cfg.instrument,
        timeframe=cfg.timeframe,
        liquidity=LiquidityTracker(cfg.liquidity_config),
        displacement=DisplacementDetector(cfg.displacement_config),
        composer=SweepDisplacementComposer(composer_cfg),
        grader=SetupGrader(),
        strategy_cfg=StrategyParams(),
    )


def _compute_stats(
    fills: list[dict],
    risk_state: RiskState,
    starting_balance: Decimal,
) -> BacktestStats:
    exits = [f for f in fills if not f["is_entry"]]
    pnls = [Decimal(f["realized_pnl_delta"]) for f in exits]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    net = sum(pnls, Decimal("0"))
    gross_win = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))

    # equity curve: accumulate ALL fills (entry commission + exit P&L)
    equity = Decimal("0")
    peak = Decimal("0")
    max_dd = Decimal("0")
    eq_curve: list[tuple[datetime, Decimal]] = []
    for f in fills:  # ALL fills, not just exits
        pnl_delta = Decimal(f["realized_pnl_delta"])
        if pnl_delta == Decimal("0"):
            continue  # skip zero-delta fills (no change to equity or curve)
        equity += pnl_delta
        ts = datetime.fromisoformat(f["ts"])
        eq_curve.append((ts, starting_balance + equity))
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    # Per-killzone breakdown: group exit fills by killzone tag.
    kz_exits: dict[str, list[Decimal]] = {}
    for f in exits:
        kz = f.get("killzone", "unknown")
        kz_exits.setdefault(kz, []).append(Decimal(f["realized_pnl_delta"]))
    by_killzone: dict[str, dict] = {}
    for kz, pnls in kz_exits.items():
        w = [p for p in pnls if p > 0]
        lo = [p for p in pnls if p < 0]
        by_killzone[kz] = {
            "trades": len(pnls),
            "wins": len(w),
            "losses": len(lo),
            "win_rate": round(len(w) / len(pnls) * 100, 1) if pnls else 0.0,
            "net_pnl": float(sum(pnls, Decimal("0"))),
        }

    # Per-side breakdown: an exit fill's side is the opposite of the trade's.
    # Counts are exit fills (partials count separately); PF is unaffected.
    side_exits: dict[str, list[Decimal]] = {}
    for f in exits:
        trade_side = "long" if f["side"] == "short" else "short"
        side_exits.setdefault(trade_side, []).append(Decimal(f["realized_pnl_delta"]))
    by_side: dict[str, dict] = {}
    for sd, side_pnls in side_exits.items():
        gw = sum((p for p in side_pnls if p > 0), Decimal("0"))
        gl = abs(sum((p for p in side_pnls if p < 0), Decimal("0")))
        by_side[sd] = {
            "exits": len(side_pnls),
            "gross_win": float(gw),
            "gross_loss": float(gl),
            "net_pnl": float(gw - gl),
            "profit_factor": float(gw / gl) if gl > 0 else None,
        }

    n = len(exits)
    profit_target = risk_state.config.profit_target

    return BacktestStats(
        trades=len(exits),
        wins=len(wins),
        losses=len(losses),
        win_rate=round(len(wins) / n * 100, 1) if n > 0 else 0.0,
        net_pnl=net,
        gross_win=gross_win,
        gross_loss=gross_loss,
        avg_win=gross_win / len(wins) if wins else Decimal("0"),
        avg_loss=gross_loss / len(losses) if losses else Decimal("0"),
        profit_factor=float(gross_win / gross_loss) if gross_loss > 0 else None,
        max_drawdown=max_dd,
        expectancy=net / n if n > 0 else Decimal("0"),
        is_profitable=net > 0,
        passed_combine=(
            net >= profit_target
            and risk_state.locked_out is None
        ),
        mll_breached=risk_state.locked_out is not None,
        equity_curve=eq_curve,
        by_killzone=by_killzone,
        by_side=by_side,
    )


def _reconstruct_trades(fills: list[dict]) -> list[dict]:
    """Pair entry fills with exit fills. hold_seconds uses fill timestamps (bar time)."""
    # Assumes strict alternation: entry fill followed by exit fill.
    # A trade still open at backtest end produces a warning and is not counted.
    trades: list[dict] = []
    open_entry: dict | None = None
    for f in fills:
        if f["is_entry"]:
            open_entry = f
        elif open_entry is not None:
            entry_ts = datetime.fromisoformat(open_entry["ts"])
            exit_ts = datetime.fromisoformat(f["ts"])
            hold = int((exit_ts - entry_ts).total_seconds())
            trade: dict = {
                "instrument": f.get("instrument", ""),
                "side": open_entry["side"],
                "size": open_entry["size"],
                "entry_ts": open_entry["ts"],
                "entry_price": open_entry["fill_price"],
                "exit_ts": f["ts"],
                "exit_price": f["fill_price"],
                "realized_pnl": f["realized_pnl_delta"],
                "hold_seconds": hold,
                "_entry_order_id": open_entry.get("order_id", ""),
            }
            if open_entry.get("grade") is not None:
                trade["grade"] = open_entry["grade"]
                trade["criteria"] = open_entry["criteria"]
            trades.append(trade)
            open_entry = None
    if open_entry is not None:
        log.warning(
            "_reconstruct_trades: unclosed entry at bar end (entry_ts=%s)",
            open_entry["ts"],
        )
    return trades


def _no_limits_risk_config(base: TopstepAccountConfig) -> TopstepAccountConfig:
    import dataclasses
    return dataclasses.replace(
        base,
        mll_initial_offset=Decimal("999999"),
        daily_loss_limit=Decimal("999999"),
        soft_buffer=Decimal("0"),
        daily_profit_limit=None,
    )


async def run_backtest(cfg: BacktestConfig) -> BacktestResult:
    """Run one backtest. Returns a BacktestResult."""
    broker = PaperBroker(
        starting_balance=cfg.starting_balance,
        slippage_ticks_market=cfg.slippage_ticks_market,
        commission_per_side=cfg.commission_per_side,
        partial_profit_r=cfg.partial_profit_r,
        max_entry_slippage_frac=cfg.max_entry_slippage_frac,
        trail_1r=cfg.trail_1r,
    )
    if cfg.strategy_params is not None and cfg.strategy_params.be_trail_r > 0:
        broker._be_trail_r = cfg.strategy_params.be_trail_r
    base_risk = fifty_k_combine(soft_buffer=cfg.soft_buffer)
    risk_state = RiskState(config=base_risk if cfg.enforce_risk_limits else _no_limits_risk_config(base_risk))
    runner = _build_runner(cfg)

    fills_captured: list[dict] = []
    signals_captured: list[dict] = []
    rejected_signals = 0
    # Maps entry order_id → killzone name so exit fills can be tagged.
    # on_signal fires after place_bracket (entry fill already emitted), so
    # entry fills get "unknown"; exit fills always get the correct killzone.
    _order_killzones: dict[str, str] = {}
    # Maps entry order_id → {"grade": str, "criteria": dict} for grade propagation.
    # Same timing constraint as killzones: populated in on_signal, applied
    # retroactively to the already-captured entry fill dict via order_id lookup
    # before _reconstruct_trades runs.
    _order_grades: dict[str, dict] = {}

    def _kz_for_fill(broker_order_id: str | None) -> str:
        if not broker_order_id:
            return "unknown"
        # Exit fills end with -X / -P / -S / -T; strip to recover entry order_id.
        bid = broker_order_id
        if len(bid) > 2 and bid[-2] == "-" and bid[-1] in "XPST":
            bid = bid[:-2]
        return _order_killzones.get(bid, "unknown")

    async def on_signal(signal: Signal, outcome: OrderOutcome) -> None:
        nonlocal rejected_signals
        if not outcome.placed:
            rejected_signals += 1
            return
        signals_captured.append({
            "ts": signal.created_at.isoformat(),
            "side": signal.side,
            "entry": str(signal.entry),
            "stop": str(signal.stop),
            "target": str(signal.target),
            "killzone": signal.killzone,
            "rationale": signal.rationale,
        })
        if outcome.broker_order_id:
            _order_killzones[outcome.broker_order_id] = signal.killzone
            g = signal.setup_grade
            if g is not None:
                _order_grades[outcome.broker_order_id] = {
                    "grade": g.grade,
                    "criteria": {
                        # mom is a bool: "strong"/"decent" pass, "weak" fails
                        "mom": g.momentum_quality != "weak",
                        "tgt": g.target_clear,
                        "fvg": g.fvg_singular,
                        "pd": g.premium_discount_ok,
                        "del": g.has_delivery_fvg,
                        "fib": str(g.fib_extension),
                    },
                }

    async def on_fill(fill: Fill) -> None:
        fill_dict: dict = {
            "ts": fill.ts.isoformat(),
            "instrument": fill.instrument,
            "side": fill.side,
            "fill_price": str(fill.fill_price),
            "size": fill.size,
            "is_entry": fill.is_entry,
            "realized_pnl_delta": str(fill.realized_pnl_delta),
            "killzone": _kz_for_fill(fill.broker_order_id),
            "order_id": fill.broker_order_id or "",
        }
        fills_captured.append(fill_dict)

    engine = ExecutionEngine(
        broker=broker,
        risk_state=risk_state,
        runners=[runner],
        on_signal=on_signal,
        replay_mode=True,
        contracts=cfg.contracts,
        risk_per_trade_pct=cfg.risk_per_trade_pct,
        # strategy_cfg drives the engine's VP gate + target override and VP.on_bar
        # accumulation. Without it the VP filter never runs (matches legacy path
        # when strategy_params is None → strategy_cfg None → gate skipped).
        strategy_cfg=cfg.strategy_params,
    )
    broker.on_fill(on_fill)

    # Faithful HTF feed: live runs an HTF refresh loop (main._refresh_htf_once)
    # that rebuilds bias/level trackers and feeds the grader's structural-target
    # gate. The backtest must replicate it from the replay stream or the grader
    # rejects every candidate ("no structural target found") → 0 trades.
    s = cfg.strategy_params
    htf_bias = htf_levels = None
    if s is not None and (s.htf_bias_enabled or s.htf_target_enabled):
        if s.htf_bias_enabled:
            htf_bias = HTFBiasTracker(lookback=s.htf_bias_lookback)
        if s.htf_target_enabled:
            htf_levels = HTFLevelFinder(swing_lookback=s.htf_bias_lookback)
        engine.htf_bias = htf_bias
        engine.htf_levels = htf_levels
    _graders = [r.grader for r in [runner] if r.grader is not None]
    _seen: deque[Bar] = deque(maxlen=_HTF_WINDOW_BARS)

    await broker.connect()
    await engine.start()

    bar_count = 0
    # Roll the trading day at 5pm CT so daily counters (DLL/DPL lockouts,
    # daily_pnl) reset like live. Without this the first DLL hit locked out
    # every remaining day of the replay — a one-day rule violation became a
    # dead run (the "risk-limit artifacts" noted in the 06-10 parity doc).
    last_trading_day: date | None = None
    for bar in cfg.bars:
        td = _trading_day_ct(bar.ts)
        if last_trading_day is None:
            last_trading_day = td
        elif td != last_trading_day:
            risk_state.roll_trading_day(bar.ts)
            last_trading_day = td
        _seen.append(bar)
        # Refresh every N bars once we have enough history.  Always runs so
        # delivery FVGs are populated even when HTF bias/target are disabled.
        if s is not None \
                and bar_count % _HTF_REFRESH_BARS == 0 and len(_seen) >= _HTF_MIN_BARS:
            _refresh_backtest_htf(list(_seen), s, htf_levels, htf_bias, _graders)
        await broker.inject_bar(bar)
        bar_count += 1

    await engine.stop()
    await broker.disconnect()

    # Retroactively attach grade to entry fill dicts now that on_signal has fired.
    for fill_dict in fills_captured:
        if fill_dict["is_entry"]:
            grade_info = _order_grades.get(fill_dict["order_id"])
            if grade_info:
                fill_dict["grade"] = grade_info["grade"]
                fill_dict["criteria"] = grade_info["criteria"]
    stats = _compute_stats(fills_captured, risk_state, cfg.starting_balance)
    trades = _reconstruct_trades(fills_captured)

    # Merge MFE/MAE from the broker's closed-excursion sidecar into each trade.
    excursions = broker.excursions_by_order_id()
    for trade in trades:
        oid = trade.pop("_entry_order_id", "")
        exc = excursions.get(oid)
        if exc is not None:
            mfe, mae, stop_dist = exc
            trade["mfe_pts"] = str(mfe)
            trade["mae_pts"] = str(mae)
            trade["r_mfe"] = round(float(mfe / stop_dist), 4) if stop_dist else 0.0
            trade["r_mae"] = round(float(mae / stop_dist), 4) if stop_dist else 0.0

    return BacktestResult(
        config=cfg,
        stats=stats,
        trades=trades,
        bars_processed=bar_count,
        rejected_signals=rejected_signals,
        label=cfg.label,
        signals=signals_captured,
    )


def _apply_sweep_dim(cfg: BacktestConfig, dim: SweepDimension, value: Any) -> BacktestConfig:
    """Return a new BacktestConfig with one param overridden."""
    if dim.container == "strategy":
        # Faithful path: vary a live StrategyParams field. _build_runner derives
        # all sub-configs (and VP) from this, so this is the only container that
        # has any effect when strategy_params is set.
        if cfg.strategy_params is None:
            raise ValueError("'strategy' sweep container requires cfg.strategy_params to be set")
        new_sp = cfg.strategy_params.model_copy(update={dim.target: value})
        return dataclasses.replace(cfg, strategy_params=new_sp)
    if dim.container == "composer":
        new_sub = dataclasses.replace(cfg.composer_config, **{dim.target: value})
        return dataclasses.replace(cfg, composer_config=new_sub)
    if dim.container == "liquidity":
        new_sub = dataclasses.replace(cfg.liquidity_config, **{dim.target: value})
        return dataclasses.replace(cfg, liquidity_config=new_sub)
    if dim.container == "displacement":
        new_sub = dataclasses.replace(cfg.displacement_config, **{dim.target: value})
        return dataclasses.replace(cfg, displacement_config=new_sub)
    raise ValueError(f"Unknown container: {dim.container!r}")


async def run_sweep(
    base: BacktestConfig,
    dims: list[SweepDimension],
    bars_factory: Callable[[], Iterator[Bar]],
) -> list[BacktestResult]:
    """Run the Cartesian product of sweep dimensions."""
    import itertools
    combos = list(itertools.product(*[d.values for d in dims]))
    results: list[BacktestResult] = []
    for combo in combos:
        cfg = base
        label_parts = []
        for dim, val in zip(dims, combo):
            cfg = _apply_sweep_dim(cfg, dim, val)
            label_parts.append(f"{dim.target}={val}")
        cfg = dataclasses.replace(cfg, bars=bars_factory(), label="  ".join(label_parts))
        results.append(await run_backtest(cfg))
    return results
