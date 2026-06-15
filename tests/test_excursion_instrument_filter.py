"""B11: ExcursionTracker instrument filter.

Defining-behavior tests:
- Two open windows on different instruments → bar for one leaves the other untouched.
- Bar with dotted SDK contract symbol (e.g. CON.F.US.MNQ.M26) normalised to root.
- Window with instrument="" (legacy) still updated by every bar (no regression).
"""
from decimal import Decimal
from datetime import datetime, timezone

from app.broker.events import Bar
from app.execution.excursion import ExcursionTracker, ExcursionWindow


def _bar(instrument: str, h, l):
    return Bar(
        instrument=instrument, timeframe="1min",
        ts=datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc),
        open=Decimal("100"), high=Decimal(h), low=Decimal(l),
        close=Decimal("100"), volume=1,
    )


def test_instrument_filter_bars_only_update_matching_window():
    """Core B11 requirement: MNQ bar must NOT update an MGC window."""
    emitted = []
    t = ExcursionTracker(emit=emitted.append)

    # Open MNQ long window and MGC short window
    t.open(key="mnq-1", kind="trade", side="long", ref=Decimal("20000"),
           target=Decimal("20010"), window_bars=3, instrument="MNQ")
    t.open(key="mgc-1", kind="trade", side="long", ref=Decimal("3300"),
           target=Decimal("3310"), window_bars=3, instrument="MGC")

    # Inject an MNQ bar — moves MNQ high to 20005, MNQ low to 19998
    t.on_bar(_bar("MNQ", "20005", "19998"))

    # MNQ window should be updated
    mnq_win = next(w for w in t._windows if w.key == "mnq-1")
    assert mnq_win.max_high == Decimal("20005")
    assert mnq_win.min_low == Decimal("19998")

    # MGC window must be UNTOUCHED
    mgc_win = next(w for w in t._windows if w.key == "mgc-1")
    assert mgc_win.max_high == Decimal("3300"), "MGC window updated by MNQ bar — instrument filter missing"
    assert mgc_win.min_low == Decimal("3300"), "MGC window updated by MNQ bar — instrument filter missing"


def test_instrument_filter_sdk_symbol_normalised():
    """Full SDK contract symbol CON.F.US.MNQ.M26 must match window instrument='MNQ'."""
    emitted = []
    t = ExcursionTracker(emit=emitted.append)

    t.open(key="mnq-sdk", kind="trade", side="long", ref=Decimal("20000"),
           target=None, window_bars=2, instrument="MNQ")

    # Bar arrives with full SDK contract string
    t.on_bar(_bar("CON.F.US.MNQ.M26", "20010", "19990"))

    mnq_win = next(w for w in t._windows if w.key == "mnq-sdk")
    assert mnq_win.max_high == Decimal("20010"), "SDK contract symbol not normalised to root"
    assert mnq_win.min_low == Decimal("19990"), "SDK contract symbol not normalised to root"


def test_instrument_filter_mgc_sdk_symbol_not_matching_mnq_window():
    """CON.F.US.MGC.M26 must NOT update an MNQ window."""
    emitted = []
    t = ExcursionTracker(emit=emitted.append)

    t.open(key="mnq-only", kind="trade", side="long", ref=Decimal("20000"),
           target=None, window_bars=2, instrument="MNQ")

    t.on_bar(_bar("CON.F.US.MGC.M26", "20010", "19990"))

    mnq_win = next(w for w in t._windows if w.key == "mnq-only")
    assert mnq_win.max_high == Decimal("20000"), "MGC SDK bar updated MNQ window"
    assert mnq_win.min_low == Decimal("20000"), "MGC SDK bar updated MNQ window"


def test_empty_instrument_window_updated_by_any_bar():
    """Legacy windows with instrument='' are not filtered — every bar updates them."""
    emitted = []
    t = ExcursionTracker(emit=emitted.append)

    # window_bars=2 → emitted after 2 bars; check via emitted list
    t.open(key="legacy", kind="trade", side="long", ref=Decimal("100"),
           target=None, window_bars=2)  # no instrument= kwarg → defaults to ""

    t.on_bar(_bar("MNQ", "105", "95"))
    t.on_bar(_bar("MGC", "108", "92"))

    assert len(emitted) == 1
    legacy_win = emitted[0]
    assert legacy_win.max_high == Decimal("108"), "legacy window (no instrument) should be updated by every bar"
    assert legacy_win.min_low == Decimal("92"), "legacy window (no instrument) should be updated by every bar"
