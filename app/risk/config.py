"""
Account configuration — verified against Topstep help docs (May 2026).

Key facts encoded here that most third-party guides get wrong:

1. The Combine MLL is TRAILING INTRADAY, not end-of-day. It follows your
   highest live equity in real time and never moves down. A session that
   prints +$3,000 then gives it all back fails the Combine, because the
   floor moved up $3,000 with the equity high.

2. The MLL stops trailing once it would equal the starting balance. After
   that point the floor is locked at $50,000 (for a $50K Combine).

3. Daily loss limit is on Net P&L during the trading day, where the
   trading day runs 5:00 PM CT to 3:10 PM CT the next calendar day.

4. The DLL is OPTIONAL on TopstepX for Combine/XFA but ENFORCED on
   non-TopstepX platforms. We treat it as enforced regardless — the bot
   should never depend on a soft Topstep setting for safety.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

# Use Decimal for money. Floats accumulate rounding error and equality
# checks against thresholds become unreliable. Every dollar amount in the
# risk module is Decimal.
Money = Decimal

AccountType = Literal["combine", "xfa", "live"]
AccountSize = Literal[50_000, 100_000, 150_000]


@dataclass(frozen=True)
class TopstepAccountConfig:
    """All account-level constants. Frozen — never mutate at runtime."""

    account_type: AccountType
    account_size: AccountSize

    # Starting balance the MLL trails from.
    starting_balance: Money

    # Initial gap from starting_balance down to the MLL floor.
    # $2,000 for $50K Combine.
    mll_initial_offset: Money

    # Daily loss limit — enforced on Net P&L within the trading day.
    # $1,000 for $50K Combine ($1,500 for $150K).
    daily_loss_limit: Money

    # Profit target to pass the Combine ($3K / $6K / $9K).
    profit_target: Money

    # Max contracts open at any time (in mini-equivalents). 50 micros = 5 minis.
    max_contracts: int

    # Soft buffer — bot stops *before* hitting the official limit.
    # Lawrence chose $500.
    soft_buffer: Money

    # Trading day boundary. Topstep resets at 5:00 PM CT (America/Chicago).
    # We store the timezone name; conversion to UTC happens in state.py.
    trading_day_reset_hour_ct: int = 17  # 5 PM Central
    trading_day_close_hour_ct: int = 15  # 3:10 PM CT — but the DLL window
    trading_day_close_minute_ct: int = 10  # closes at 3:10 PM CT


def fifty_k_combine(soft_buffer: Money = Decimal("500")) -> TopstepAccountConfig:
    """Lawrence's account: $50K Combine, $500 soft buffer."""
    return TopstepAccountConfig(
        account_type="combine",
        account_size=50_000,
        starting_balance=Decimal("50000"),
        mll_initial_offset=Decimal("2000"),
        daily_loss_limit=Decimal("1000"),
        profit_target=Decimal("3000"),
        max_contracts=5,  # mini-equivalent; 50 micros also OK
        soft_buffer=soft_buffer,
    )


def config_for_account(
    account_name: str,
    broker_balance: Money,
    soft_buffer: Money = Decimal("500"),
) -> TopstepAccountConfig:
    """
    Build the right risk config from the live account name and current balance.

    Account name prefixes (TopstepX convention):
      50KTC-*   → $50K Topstep Challenge (Combine)
      100KTC-*  → $100K Combine
      150KTC-*  → $150K Combine
      PRAC-*    → Practice account (permissive limits)
      EXPRESS-* → Express Funded Account

    For practice/express we set wide safety margins using the actual balance.
    For combines, starting_balance is the challenge size (not current balance),
    since MLL trails from the original starting point.
    """
    name = account_name.upper()

    if "150KTC" in name:
        return TopstepAccountConfig(
            account_type="combine",
            account_size=150_000,
            starting_balance=Decimal("150000"),
            mll_initial_offset=Decimal("4500"),
            daily_loss_limit=Decimal("2500"),
            profit_target=Decimal("9000"),
            max_contracts=15,
            soft_buffer=soft_buffer,
        )
    if "100KTC" in name:
        return TopstepAccountConfig(
            account_type="combine",
            account_size=100_000,
            starting_balance=Decimal("100000"),
            mll_initial_offset=Decimal("3000"),
            daily_loss_limit=Decimal("2000"),
            profit_target=Decimal("6000"),
            max_contracts=10,
            soft_buffer=soft_buffer,
        )
    if "50KTC" in name:
        return fifty_k_combine(soft_buffer)

    # Practice / Express / unknown — use actual balance, wide limits.
    # 4% trailing drawdown, 1% daily loss limit.
    bal = broker_balance if broker_balance > 0 else Decimal("50000")
    return TopstepAccountConfig(
        account_type="xfa",
        account_size=int(bal),
        starting_balance=bal,
        mll_initial_offset=bal * Decimal("0.04"),
        daily_loss_limit=bal * Decimal("0.01"),
        profit_target=bal,  # no profit target for practice/express
        max_contracts=20,
        soft_buffer=soft_buffer,
    )
