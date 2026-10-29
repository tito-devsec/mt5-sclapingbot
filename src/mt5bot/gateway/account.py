"""Account guard: the system refuses to run unless the account is a USD demo account."""

from __future__ import annotations

from dataclasses import dataclass

from mt5bot.core.errors import AccountValidationError
from mt5bot.gateway.base import AccountSnapshot


@dataclass(frozen=True)
class AccountCheck:
    ok: bool
    code: str
    detail: str


def check_account(account: AccountSnapshot | None, expected_currency: str) -> list[AccountCheck]:
    if account is None:
        return [AccountCheck(False, "ACCOUNT_UNAVAILABLE", "terminal returned no account information")]
    checks = [
        AccountCheck(
            account.is_demo,
            "ACCOUNT_NOT_DEMO" if not account.is_demo else "ACCOUNT_DEMO",
            f"trade mode is {account.trade_mode_name}",
        ),
        AccountCheck(
            account.currency.upper() == expected_currency.upper(),
            "ACCOUNT_CURRENCY_MISMATCH"
            if account.currency.upper() != expected_currency.upper()
            else "ACCOUNT_CURRENCY_OK",
            f"account currency {account.currency}, expected {expected_currency}",
        ),
    ]
    return checks


def require_valid_account(account: AccountSnapshot | None, expected_currency: str) -> AccountSnapshot:
    """Fail closed: raise unless every account check passes."""
    failures = [c for c in check_account(account, expected_currency) if not c.ok]
    if failures:
        raise AccountValidationError("; ".join(f"{c.code}: {c.detail}" for c in failures), code=failures[0].code)
    assert account is not None
    return account
