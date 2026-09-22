"""session_pnl Option B: account-scoped CSV fallback.

WHY: `_daily_pnl_from_csv` is the fallback that seeds the daily-loss gate when
the TopstepX /Trade/search call fails. It sums EXIT rows in the session window
from the master trades.csv — but that ledger accumulates rows across accounts
over time (shadow practice -> live combine switches). Without an account filter,
a session that spans an account switch would seed the loss gate with another
account's P&L. The `account` column lets the fallback count only the live
account's trades. Backward-compatible: rows written before the column existed
(no `account` value) are still counted, so old ledgers don't silently zero out.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal

from app.builders import _daily_pnl_from_csv


def _write_csv(path, rows, header):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


_SESSION_START = datetime(2026, 6, 16, 22, 0, tzinfo=timezone.utc)  # 5pm CT roll
_IN_WINDOW = "2026-06-16T23:30:00+00:00"


class TestReaderAccountFilter:
    def test_filters_to_matching_account(self, tmp_path):
        p = tmp_path / "trades.csv"
        _write_csv(
            p,
            rows=[
                [_IN_WINDOW, "EXIT", "100", "111"],
                [_IN_WINDOW, "EXIT", "-50", "222"],
            ],
            header=["ts", "type", "realized_pnl", "account"],
        )
        # Only account 111's +100 should count.
        assert _daily_pnl_from_csv(_SESSION_START, account_id="111", csv_path=p) == Decimal("100")
        assert _daily_pnl_from_csv(_SESSION_START, account_id="222", csv_path=p) == Decimal("-50")

    def test_no_account_id_sums_all(self, tmp_path):
        p = tmp_path / "trades.csv"
        _write_csv(
            p,
            rows=[
                [_IN_WINDOW, "EXIT", "100", "111"],
                [_IN_WINDOW, "EXIT", "-50", "222"],
            ],
            header=["ts", "type", "realized_pnl", "account"],
        )
        # No account scoping -> legacy behavior, sum everything.
        assert _daily_pnl_from_csv(_SESSION_START, account_id=None, csv_path=p) == Decimal("50")

    def test_legacy_rows_without_account_still_counted(self, tmp_path):
        # Ledger written before the account column existed: filtering by account
        # must NOT drop these rows (defense-in-depth, not a regression).
        p = tmp_path / "trades.csv"
        _write_csv(
            p,
            rows=[[_IN_WINDOW, "EXIT", "75"]],
            header=["ts", "type", "realized_pnl"],
        )
        assert _daily_pnl_from_csv(_SESSION_START, account_id="111", csv_path=p) == Decimal("75")


class TestWriterEmitsAccount:
    def test_append_fill_csv_writes_account_column(self, tmp_path, monkeypatch):
        import app.journaling as J
        from app.sim.events import Fill

        master = tmp_path / "trades.csv"
        daily = tmp_path / "trades_daily.csv"
        monkeypatch.setattr(J, "_TRADES_CSV", master)
        monkeypatch.setattr(J, "_daily_csv_path", lambda: daily)

        fill = Fill(
            ts=datetime(2026, 6, 16, 23, 0, tzinfo=timezone.utc),
            instrument="MNQ", side="long",
            fill_price=Decimal("100"), size=1, is_entry=False,
            realized_pnl_delta=Decimal("42"), contracts_delta=-1,
            broker_order_id="oid-1",
        )
        J._append_fill_csv(fill, account_id=999)

        assert J._TRADES_HEADERS[-1] == "account"
        with open(master, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert rows[-1]["account"] == "999"
