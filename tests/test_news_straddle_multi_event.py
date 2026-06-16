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


from datetime import date
from app.strategy.cpi_day import suppress_dates, all_event_dates
from decimal import Decimal as D


def _specs():
    return [
        ResolvedStraddleSpec("CPI", "MNQ", 60, D("3.0"), 1, suppress_base=False),
        ResolvedStraddleSpec("FOMC", "MGC", 20, D("3.0"), 20, suppress_base=True),
    ]


def test_suppress_dates_only_includes_suppress_base_specs(tmp_path):
    csv = tmp_path / "news.csv"
    csv.write_text(
        "event_type,ts_utc\n"
        "CPI,2026-07-14T12:30:00+00:00\n"
        "FOMC,2026-06-17T18:00:00+00:00\n"
    )
    sd = suppress_dates(_specs(), str(csv))
    assert sd == frozenset({date(2026, 6, 17)})          # FOMC only (suppress_base=True)
    assert date(2026, 7, 14) not in sd                    # CPI stays additive


def test_all_event_dates_is_union(tmp_path):
    csv = tmp_path / "news.csv"
    csv.write_text(
        "event_type,ts_utc\n"
        "CPI,2026-07-14T12:30:00+00:00\n"
        "FOMC,2026-06-17T18:00:00+00:00\n"
    )
    ad = all_event_dates(_specs(), str(csv))
    assert ad == frozenset({date(2026, 7, 14), date(2026, 6, 17)})


from app.strategy.news_straddle import build_news_straddle_schedulers


def test_build_schedulers_one_per_spec_with_right_params(tmp_path):
    csv = tmp_path / "news.csv"
    csv.write_text(
        "event_type,ts_utc\n"
        "CPI,2026-07-14T12:30:00+00:00\n"
        "FOMC,2026-06-17T18:00:00+00:00\n"
    )
    specs = [
        ResolvedStraddleSpec("CPI", "MNQ", 60, D("3.0"), 1, suppress_base=False),
        ResolvedStraddleSpec("FOMC", "MGC", 20, D("3.0"), 20, suppress_base=True),
    ]
    scheds = build_news_straddle_schedulers(
        broker=object(), specs=specs, events_path=str(csv), arm_lead_seconds=120,
    )
    assert [s.instrument for s in scheds] == ["MNQ", "MGC"]
    assert scheds[1].size == 20
    # offset = offset_ticks * tick; MGC tick = 0.10 → 20 * 0.10 = 2.0
    assert scheds[1].offset == D("2.0")
    # MNQ tick = 0.25 → 60 * 0.25 = 15.0
    assert scheds[0].offset == D("15.0")
    # each scheduler only loaded its own event_type
    assert len(scheds[1]._events) == 1
