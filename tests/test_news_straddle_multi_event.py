from decimal import Decimal
from app.bot_config import StrategyParams, NewsStraddleEvent


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
