from datetime import datetime, timezone, date

from app.strategy.cpi_day import is_cpi_day, next_cpi_date, router_state


# A CPI release is 08:30 ET. In UTC that is 12:30 (EDT) — same calendar day in ET.
CPI_DATES = frozenset({date(2026, 7, 15), date(2026, 8, 12)})


def test_is_cpi_day_true_on_release_date():
    ts = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)  # 10:00 ET on a CPI day
    assert is_cpi_day(ts, CPI_DATES) is True


def test_is_cpi_day_false_day_before_and_after():
    assert is_cpi_day(datetime(2026, 7, 14, 14, 0, tzinfo=timezone.utc), CPI_DATES) is False
    assert is_cpi_day(datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc), CPI_DATES) is False


def test_is_cpi_day_et_vs_utc_boundary():
    # 2026-07-16 00:30 UTC is 2026-07-15 20:30 ET — still the CPI calendar day in ET.
    # A naive UTC-date check would wrongly read this as 07-16 and miss it.
    ts = datetime(2026, 7, 16, 0, 30, tzinfo=timezone.utc)
    assert is_cpi_day(ts, CPI_DATES) is True


def test_is_cpi_day_empty_set_is_false():
    assert is_cpi_day(datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc), frozenset()) is False


def test_is_cpi_day_naive_ts_assumed_utc():
    # Defensive: a naive ts is treated as UTC (matches killzone.py convention).
    assert is_cpi_day(datetime(2026, 7, 15, 14, 0), CPI_DATES) is True


def test_next_cpi_date_returns_today_if_cpi_day():
    ts = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)
    assert next_cpi_date(ts, CPI_DATES) == date(2026, 7, 15)


def test_next_cpi_date_returns_future_when_none_today():
    ts = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
    assert next_cpi_date(ts, CPI_DATES) == date(2026, 8, 12)


def test_next_cpi_date_none_when_all_past():
    ts = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
    assert next_cpi_date(ts, CPI_DATES) is None


def test_router_state_shape_on_cpi_day():
    ts = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)
    st = router_state(ts, CPI_DATES)
    assert st == {
        "today_is_cpi_day": True,
        "next_cpi_date": "2026-07-15",
        "base_entries_suppressed": True,
    }


def test_router_state_shape_off_day():
    ts = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
    st = router_state(ts, CPI_DATES)
    assert st["today_is_cpi_day"] is False
    assert st["base_entries_suppressed"] is False
    assert st["next_cpi_date"] == "2026-08-12"
