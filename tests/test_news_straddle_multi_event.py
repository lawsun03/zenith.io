from decimal import Decimal
from app.bot_config import StrategyParams, NewsStraddleEvent
from app.strategy.news_straddle import resolve_straddle_specs, ResolvedStraddleSpec


def test_news_straddle_events_parses_from_dicts():
    sp = StrategyParams(news_straddle_events=[
        {"event_type": "CPI", "instrument": "MNQ", "offset_ticks": 60, "tp_r": "3.0",
         "contracts": 1, "suppress_base": False},
        {"event_type": "FOMC", "instrument": "MGC", "offset_ticks": 20, "tp_r": "3.0",
         "contracts": 20, "suppress_base": True},
    ])
    assert len(sp.news_straddle_events) == 2
    fomc = sp.news_straddle_events[1]
    assert isinstance(fomc, NewsStraddleEvent)
    assert fomc.instrument == "MGC"
    assert fomc.offset_ticks == 20
    assert fomc.tp_r == Decimal("3.0")
    assert fomc.contracts == 20
    assert fomc.suppress_base is True


def test_news_straddle_events_defaults_empty():
    sp = StrategyParams()
    assert sp.news_straddle_events == []


def test_resolve_uses_events_list_when_present():
    sp = StrategyParams(news_straddle_events=[
        {"event_type": "CPI", "instrument": "MNQ", "offset_ticks": 60, "contracts": 1},
        {"event_type": "FOMC", "instrument": "MGC", "offset_ticks": 20, "contracts": 20,
         "suppress_base": True},
    ])
    specs = resolve_straddle_specs(sp, default_instrument="MNQ")
    assert [s.instrument for s in specs] == ["MNQ", "MGC"]
    assert specs[1].event_type == "FOMC"
    assert specs[1].contracts == 20
    assert specs[1].suppress_base is True


def test_resolve_falls_back_to_legacy_single_event():
    sp = StrategyParams(news_straddle_event_type="CPI", news_straddle_offset_ticks=60,
                        news_straddle_contracts=1, cpi_base_suppress=False)
    specs = resolve_straddle_specs(sp, default_instrument="MNQ")
    assert len(specs) == 1
    assert specs[0].event_type == "CPI"
    assert specs[0].instrument == "MNQ"
    assert specs[0].offset_ticks == 60
    assert specs[0].suppress_base is False
