from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.server import build_app
from app.bot_config import BotConfig, StrategyParams, save_bot_config
from app.risk.config import fifty_k_combine
from app.risk.state import RiskState


class _Broker:
    def __init__(self):
        self.entry_mode = "market"
        self.partial_profit_r = Decimal("0")


def _client(tmp_path: Path, broker):
    cfg_path = tmp_path / "bot_config.json"
    save_bot_config(BotConfig(partial_profit_r=Decimal("1.5")), cfg_path)
    app = build_app(
        risk_state=RiskState(config=fifty_k_combine()),
        reconciler=None, journal=None, static_dir=None,
        bot_config_path=cfg_path, effective_instrument="MGC",
        effective_timeframes=["1min"], mode="live", broker=broker,
    )
    return TestClient(app)


def test_get_config_returns_partial_profit_r(tmp_path):
    client = _client(tmp_path, _Broker())
    r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json()["partial_profit_r"] == 1.5


def test_patch_hot_applies_partial_profit_r(tmp_path):
    broker = _Broker()
    client = _client(tmp_path, broker)
    body = BotConfig(partial_profit_r=Decimal("1.5")).model_dump(mode="json")
    r = client.patch("/api/config", json=body)
    assert r.status_code == 200
    assert r.json()["partial_profit_r"] == 1.5
    assert broker.partial_profit_r == Decimal("1.5")


def test_get_config_returns_ifvg_defaults(tmp_path):
    """GET /api/config must include ifvg_entry_mode and ifvg_stop_buffer_ticks."""
    client = _client(tmp_path, _Broker())
    r = client.get("/api/config")
    assert r.status_code == 200
    strategy = r.json()["strategy"]
    assert strategy["ifvg_entry_mode"] == "ifvg_edge"
    assert strategy["ifvg_stop_buffer_ticks"] == "1.0"


def test_patch_updates_ifvg_fields(tmp_path):
    """PATCH /api/config must round-trip new iFVG fields."""
    client = _client(tmp_path, _Broker())
    body = BotConfig(
        partial_profit_r=Decimal("0"),
        strategy=StrategyParams(
            ifvg_entry_mode="retrace_ce",
            ifvg_stop_buffer_ticks=Decimal("2.5"),
        ),
    ).model_dump(mode="json")
    r = client.patch("/api/config", json=body)
    assert r.status_code == 200
    strategy = r.json()["strategy"]
    assert strategy["ifvg_entry_mode"] == "retrace_ce"
    assert strategy["ifvg_stop_buffer_ticks"] == "2.5"
