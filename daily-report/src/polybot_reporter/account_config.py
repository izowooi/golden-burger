"""Dynamic account-slot discovery for Jenkins and local report runs."""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from collections.abc import Mapping

logger = logging.getLogger(__name__)

_ACCOUNT_ENV_PATTERN = re.compile(r"^ACCOUNT_([1-9]\d*)_(NAME|ADDRESS)$")
_REPORT_ACCOUNT_ORDER_ENV = "REPORT_ACCOUNT_ORDER"


class AccountConfigurationError(ValueError):
    """Raised when numeric account slots are incomplete or ambiguous."""


class AccountConfig:
    """Configuration for a single account."""

    def __init__(self, name: str, address: str, slack_name: str | None = None):
        self.name = name
        self.address = address
        self.display_name = name
        self.slack_name = slack_name

    def __repr__(self) -> str:
        return f"AccountConfig(name={self.name}, address=[REDACTED])"


def load_account_configs(environ: Mapping[str, str] | None = None) -> list[AccountConfig]:
    """Discover numeric account slots and apply an optional explicit report order."""
    values = os.environ if environ is None else environ
    account_slots = sorted(
        {int(match.group(1)) for key in values if (match := _ACCOUNT_ENV_PATTERN.fullmatch(key))}
    )
    accounts: list[AccountConfig] = []

    for slot in account_slots:
        name = values.get(f"ACCOUNT_{slot}_NAME", "").strip()
        address = values.get(f"ACCOUNT_{slot}_ADDRESS", "").strip()
        slack_name = values.get(f"ACCOUNT_{slot}_SLACK_NAME", "").strip() or None
        if name and address:
            accounts.append(
                AccountConfig(name=name, address=address, slack_name=slack_name)
            )
            logger.info("계좌 %d 로드 완료: %s (address=[REDACTED])", slot, name)
        elif name or address:
            raise AccountConfigurationError(
                f"계좌 {slot} 설정이 불완전합니다: "
                f"NAME={bool(name)}, ADDRESS={bool(address)}"
            )

    if not accounts:
        logger.error("환경변수에서 계좌 설정을 찾을 수 없습니다")
        logger.error("ACCOUNT_1_NAME, ACCOUNT_1_ADDRESS 등을 설정하세요")
        return accounts

    name_counts = Counter(account.name for account in accounts)
    name_indices: dict[str, int] = {}
    for account in accounts:
        if name_counts[account.name] > 1:
            name_indices[account.name] = name_indices.get(account.name, 0) + 1
            account.display_name = f"{account.name} ({name_indices[account.name]})"
        if account.slack_name is None:
            account.slack_name = account.display_name

    normalized_slack_names = [
        " ".join(account.slack_name.upper().split()) for account in accounts
    ]
    if len(normalized_slack_names) != len(set(normalized_slack_names)):
        raise AccountConfigurationError("Slack 계정 표시 이름이 중복됩니다")
    normalized_addresses = [account.address.lower() for account in accounts]
    if len(normalized_addresses) != len(set(normalized_addresses)):
        raise AccountConfigurationError("동일한 wallet address가 여러 계정 slot에 있습니다")

    raw_report_order = values.get(_REPORT_ACCOUNT_ORDER_ENV, "").strip()
    if not raw_report_order:
        return accounts

    report_order = [name.strip() for name in raw_report_order.split(",")]
    if any(not name for name in report_order):
        raise AccountConfigurationError(
            f"{_REPORT_ACCOUNT_ORDER_ENV}에 빈 계정 이름이 있습니다"
        )
    if len(report_order) != len(set(report_order)):
        raise AccountConfigurationError(
            f"{_REPORT_ACCOUNT_ORDER_ENV}에 중복 계정 이름이 있습니다"
        )

    accounts_by_display_name = {account.display_name: account for account in accounts}
    configured_names = set(accounts_by_display_name)
    ordered_names = set(report_order)
    if configured_names != ordered_names:
        raise AccountConfigurationError(
            f"{_REPORT_ACCOUNT_ORDER_ENV}가 전체 계정의 정확한 순열이 아닙니다: "
            f"missing={sorted(configured_names - ordered_names)}, "
            f"unknown={sorted(ordered_names - configured_names)}"
        )

    return [accounts_by_display_name[name] for name in report_order]
