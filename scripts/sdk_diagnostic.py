"""
SDK diagnostic — run ONCE against a TopstepX demo account before
trusting the live wrapper.

What this script does:
  1. Connects to TopstepX via project_x_py.
  2. Probes every SDK touchpoint topstepx.py depends on.
  3. Reports PASS/FAIL/SUSPECT for each, with the actual values seen.
  4. Identifies the exact field names your installed SDK version uses
     (which may differ from what topstepx.py assumes).

Usage:
    PROJECT_X_USERNAME=... PROJECT_X_API_KEY=... \\
    python scripts/sdk_diagnostic.py --instrument MGC

If a check is SUSPECT, the script prints the SDK object's actual
attributes/keys so you can update topstepx.py directly. Don't proceed
to live trading until every check is PASS.

What it does NOT do:
  - Place real orders. Even on demo, we don't want side effects.
  - Test the full bot loop. That's what paper mode is for.
  - Hit production endpoints. SDK should be configured for demo.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class CheckResult:
    name: str
    status: str            # PASS | FAIL | SUSPECT | SKIP
    detail: str = ""
    raw: Any = None


class Reporter:
    """Collects results and prints a summary."""

    def __init__(self) -> None:
        self.results: list[CheckResult] = []

    def record(self, r: CheckResult) -> None:
        self.results.append(r)
        symbol = {"PASS": "✓", "FAIL": "✗", "SUSPECT": "?", "SKIP": "·"}[r.status]
        print(f"  [{symbol}] {r.name:40s} {r.status:8s} {r.detail}")

    def summary(self) -> int:
        """Return exit code: 0 if all PASS, 1 otherwise."""
        print()
        print("=" * 70)
        counts = {}
        for r in self.results:
            counts[r.status] = counts.get(r.status, 0) + 1
        print(f"Results: " + ", ".join(
            f"{k}={v}" for k, v in sorted(counts.items())
        ))

        fails = [r for r in self.results if r.status == "FAIL"]
        suspects = [r for r in self.results if r.status == "SUSPECT"]

        if fails:
            print()
            print("FAILURES — fix before going live:")
            for r in fails:
                print(f"  - {r.name}: {r.detail}")

        if suspects:
            print()
            print("SUSPECT — verify these manually:")
            for r in suspects:
                print(f"  - {r.name}: {r.detail}")
                if r.raw is not None:
                    print(f"      raw: {r.raw!r}")

        if not fails and not suspects:
            print()
            print("All checks passed. Live wrapper should work.")
            return 0
        return 1


# ---------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------

async def check_sdk_imports(reporter: Reporter) -> Any:
    """Can we import project_x_py at all?"""
    try:
        import project_x_py  # noqa
        reporter.record(CheckResult(
            name="project_x_py importable",
            status="PASS",
            detail=f"version={getattr(project_x_py, '__version__', 'unknown')}",
        ))
        return project_x_py
    except ImportError as e:
        reporter.record(CheckResult(
            name="project_x_py importable",
            status="FAIL",
            detail=f"pip install project-x-py — {e}",
        ))
        return None


async def check_credentials_present(reporter: Reporter) -> bool:
    user = os.environ.get("PROJECT_X_USERNAME")
    key = os.environ.get("PROJECT_X_API_KEY")
    if user and key:
        reporter.record(CheckResult(
            name="credentials in environment",
            status="PASS",
            detail=f"username={user[:3]}*** key=set",
        ))
        return True
    reporter.record(CheckResult(
        name="credentials in environment",
        status="FAIL",
        detail="PROJECT_X_USERNAME or PROJECT_X_API_KEY missing",
    ))
    return False


async def check_trading_suite_create(
    reporter: Reporter,
    sdk: Any,
    instrument: str,
) -> Any:
    """The fundamental call topstepx.py makes."""
    try:
        suite = await sdk.TradingSuite.create(
            instrument=instrument,
            timeframes=["1min"],
        )
        reporter.record(CheckResult(
            name="TradingSuite.create",
            status="PASS",
            detail=f"connected, instrument={instrument}",
        ))
        return suite
    except Exception as e:
        reporter.record(CheckResult(
            name="TradingSuite.create",
            status="FAIL",
            detail=f"{type(e).__name__}: {e}",
        ))
        return None


async def check_account_balance(reporter: Reporter, suite: Any) -> None:
    """topstepx.py reads suite.client.account_info.balance."""
    try:
        balance = suite.client.account_info.balance
        if isinstance(balance, (int, float)):
            reporter.record(CheckResult(
                name="account_info.balance",
                status="PASS",
                detail=f"value={balance} type={type(balance).__name__}",
            ))
        else:
            reporter.record(CheckResult(
                name="account_info.balance",
                status="SUSPECT",
                detail=f"unexpected type: {type(balance).__name__}",
                raw=balance,
            ))
    except AttributeError as e:
        # Find the real attribute path.
        info = getattr(suite.client, "account_info", None)
        attrs = dir(info) if info else dir(suite.client)
        money_attrs = [a for a in attrs if "balance" in a.lower() or "equity" in a.lower()]
        reporter.record(CheckResult(
            name="account_info.balance",
            status="FAIL",
            detail=(
                f"AttributeError. Look for one of: {money_attrs}. "
                f"Update TopstepXBroker.account_balance() accordingly."
            ),
            raw=money_attrs,
        ))


async def check_get_positions(reporter: Reporter, suite: Any) -> None:
    """topstepx.py calls suite.positions.get_all_positions()."""
    try:
        positions = await suite.positions.get_all_positions()
        reporter.record(CheckResult(
            name="positions.get_all_positions",
            status="PASS",
            detail=f"returned {len(positions)} positions",
        ))

        if positions:
            # Probe the first position's attributes.
            p = positions[0]
            attrs = {a: getattr(p, a, "<missing>") for a in (
                "contract_id", "side", "size",
                "average_price", "averagePrice",
                "unrealized_pnl", "unrealizedPnl", "pnl_unrealized",
            )}
            present = {k: v for k, v in attrs.items() if v != "<missing>"}
            reporter.record(CheckResult(
                name="position attributes",
                status="SUSPECT",
                detail="check which fields actually exist",
                raw=present,
            ))
        else:
            reporter.record(CheckResult(
                name="position attributes",
                status="SKIP",
                detail="no open positions to probe — open one in TopstepX UI then re-run",
            ))
    except Exception as e:
        reporter.record(CheckResult(
            name="positions.get_all_positions",
            status="FAIL",
            detail=f"{type(e).__name__}: {e}",
        ))


async def check_event_types(reporter: Reporter, sdk: Any) -> None:
    """Which EventType values does this SDK expose? topstepx.py needs
    NEW_BAR for sure, and one of ORDER_FILLED/FILL/POSITION_CHANGED."""
    try:
        EventType = sdk.EventType
        names = [n for n in dir(EventType) if n.isupper()]

        bar_evt = "NEW_BAR" in names
        fill_evts = [n for n in ("ORDER_FILLED", "FILL", "POSITION_CHANGED")
                     if n in names]

        if bar_evt and fill_evts:
            reporter.record(CheckResult(
                name="EventType has NEW_BAR + fill events",
                status="PASS",
                detail=f"NEW_BAR=yes, fill candidates: {fill_evts}",
            ))
        else:
            reporter.record(CheckResult(
                name="EventType has NEW_BAR + fill events",
                status="SUSPECT",
                detail=f"available: {names}",
                raw=names,
            ))
    except Exception as e:
        reporter.record(CheckResult(
            name="EventType enumeration",
            status="FAIL",
            detail=f"{type(e).__name__}: {e}",
        ))


async def check_order_methods_exist(reporter: Reporter, suite: Any) -> None:
    """Confirm the order methods topstepx.py calls actually exist."""
    methods_needed = [
        ("orders.place_bracket_order", suite.orders, "place_bracket_order"),
        ("orders.place_market_order",  suite.orders, "place_market_order"),
        ("orders.cancel_all_orders",   suite.orders, "cancel_all_orders"),
    ]
    for name, obj, attr in methods_needed:
        if hasattr(obj, attr) and callable(getattr(obj, attr)):
            reporter.record(CheckResult(
                name=name,
                status="PASS",
                detail="callable",
            ))
        else:
            available = [a for a in dir(obj) if not a.startswith("_")]
            reporter.record(CheckResult(
                name=name,
                status="FAIL",
                detail=f"missing. available: {available}",
                raw=available,
            ))


async def check_event_bus_register(reporter: Reporter, suite: Any) -> None:
    """The @suite.events.on(...) decorator must work."""
    try:
        # Don't actually attach a handler; just confirm the API surface.
        on = getattr(suite.events, "on", None)
        if on is None or not callable(on):
            reporter.record(CheckResult(
                name="suite.events.on",
                status="FAIL",
                detail="no .on(...) method on events bus",
                raw=dir(suite.events),
            ))
        else:
            reporter.record(CheckResult(
                name="suite.events.on",
                status="PASS",
                detail="registration API present",
            ))
    except Exception as e:
        reporter.record(CheckResult(
            name="suite.events.on",
            status="FAIL",
            detail=f"{type(e).__name__}: {e}",
        ))


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

async def run(instrument: str) -> int:
    print("=" * 70)
    print(f"topstep-bot SDK diagnostic — instrument={instrument}")
    print(f"timestamp={datetime.now().isoformat()}")
    print("=" * 70)

    reporter = Reporter()

    # Pre-checks.
    if not await check_credentials_present(reporter):
        return reporter.summary()

    sdk = await check_sdk_imports(reporter)
    if sdk is None:
        return reporter.summary()

    # Connection check.
    suite = await check_trading_suite_create(reporter, sdk, instrument)
    if suite is None:
        return reporter.summary()

    try:
        await check_account_balance(reporter, suite)
        await check_get_positions(reporter, suite)
        await check_event_types(reporter, sdk)
        await check_order_methods_exist(reporter, suite)
        await check_event_bus_register(reporter, suite)
    finally:
        # Always disconnect.
        try:
            await suite.disconnect()
        except Exception:
            pass

    return reporter.summary()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--instrument", default="MGC",
        help="Instrument symbol to probe (default: MGC)",
    )
    args = parser.parse_args()

    # Suppress noisy third-party logs during diagnostic.
    logging.basicConfig(level=logging.WARNING)

    return asyncio.run(run(args.instrument))


if __name__ == "__main__":
    sys.exit(main())
