"""Dynamic account-slot discovery for Jenkins and local report runs."""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from collections.abc import Mapping

logger = logging.getLogger(__name__)

_ACCOUNT_ENV_PATTERN = re.compile(r"^ACCOUNT_([1-9]\d*)_(NAME|ADDRESS)$")


class AccountConfigurationError(ValueError):
    """Raised when numeric account slots are incomplete or ambiguous."""


class AccountConfig:
    """Configuration for a single account."""

    def __init__(self, name: str, address: str):
        self.name = name
        self.address = address
        self.display_name = name

    def __repr__(self) -> str:
        return f"AccountConfig(name={self.name}, address=[REDACTED])"


def load_account_configs(environ: Mapping[str, str] | None = None) -> list[AccountConfig]:
    """Discover every numeric ACCOUNT_<slot> NAME/ADDRESS pair in slot order."""
    values = os.environ if environ is None else environ
    account_slots = sorted(
        {int(match.group(1)) for key in values if (match := _ACCOUNT_ENV_PATTERN.fullmatch(key))}
    )
    accounts: list[AccountConfig] = []

    for slot in account_slots:
        name = values.get(f"ACCOUNT_{slot}_NAME", "").strip()
        address = values.get(f"ACCOUNT_{slot}_ADDRESS", "").strip()
        if name and address:
            accounts.append(AccountConfig(name=name, address=address))
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
    normalized_addresses = [account.address.lower() for account in accounts]
    if len(normalized_addresses) != len(set(normalized_addresses)):
        raise AccountConfigurationError("동일한 wallet address가 여러 계정 slot에 있습니다")
    return accounts
