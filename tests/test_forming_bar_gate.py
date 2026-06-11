"""
forming_bar_entries gate on the engine's forming-bar poller.

Why this matters: the walk-forward validation only covers entries confirmed
by a CLOSED b3 bar. The poller enters mid-bar on a touch — an unvalidated
path that diverged from sim on 2026-06-11. Default must be off, and the
flag must be hot-appliable (checked per poll tick, not at task creation).
"""
import asyncio
from decimal import Decimal

import pytest

from app.bot_config import BotConfig
from app.broker.paper import PaperBroker
from app.execution.engine import ExecutionEngine
from app.risk.config import fifty_k_combine
from app.risk.state import RiskState

from tests.test_engine import make_runner


def test_default_is_closed_bar_only():
    assert BotConfig().forming_bar_entries is False


def test_round_trip(tmp_path):
    from app.bot_config import load_bot_config, save_bot_config
    p = tmp_path / "cfg.json"
    save_bot_config(BotConfig(forming_bar_entries=True), p)
    assert load_bot_config(p).forming_bar_entries is True


@pytest.mark.asyncio
async def test_poller_respects_flag_and_hot_apply():
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    engine = ExecutionEngine(
        broker, state, [make_runner()],
        forming_bar_entries=False,  # validated closed-bar behavior
    )
    calls = 0

    async def fake_get_forming_bar(timeframe):
        nonlocal calls
        calls += 1
        return None

    broker.get_forming_bar = fake_get_forming_bar

    await broker.connect()
    await engine.start()
    try:
        await asyncio.sleep(2.5)
        assert calls == 0, "poller queried the forming bar while gated off"

        # Hot-apply (what PATCH /api/config does) — no restart, no new task.
        engine.forming_bar_entries = True
        await asyncio.sleep(2.5)
        assert calls > 0, "poller did not resume after hot-apply"
    finally:
        await engine.stop()
