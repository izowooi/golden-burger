"""Stable identifiers shared by reporting, Slack, and local evidence storage."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

PORTFOLIO_REPORT_SCHEMA_VERSION = "pb-portfolio/v2"
PORTFOLIO_ERROR_SCHEMA_VERSION = "pb-portfolio/error-v1"

ACCOUNT_ID_BY_DISPLAY_NAME = {
    "GOLDEN-APPLE (1)": "golden-apple-1",
    "GOLDEN-BANANA": "golden-banana",
    "GOLDEN-CHERRY": "golden-cherry",
    "GOLDEN-APPLE (2)": "golden-apple-2",
    "GOLDEN-ECO": "golden-eco",
    "GOLDEN-FOX": "golden-fox",
}
CURRENT_ACCOUNT_DISPLAY_NAMES = frozenset(ACCOUNT_ID_BY_DISPLAY_NAME)
_CHAIN_IDENTIFIER_RE = re.compile(
    r"0x(?:[0-9a-fA-F]{64}|[0-9a-fA-F]{40})(?![0-9a-fA-F])"
)
_SLACK_WEBHOOK_RE = re.compile(
    r"https://hooks\.slack\.com/services/[^\s'\"\])}]+", re.IGNORECASE
)
_SUPABASE_SECRET_RE = re.compile(r"\bsb_secret_[A-Za-z0-9._-]+", re.IGNORECASE)
_SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]+", re.IGNORECASE)
_AUTH_SCHEME_RE = re.compile(
    r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE
)
_DATABASE_DSN_RE = re.compile(
    r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb|redis|rediss|mssql|oracle)"
    r"(?:\+[A-Za-z0-9._-]+)?://"
    r"[^\s'\"\])}]+",
    re.IGNORECASE,
)
_GITHUB_TOKEN_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
)
_AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_OPENAI_TOKEN_RE = re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b")
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"([\"']?(?:authorization|proxy-authorization|x-api-key|api[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|secret[_-]?key|client[_-]?secret|"
    r"aws[_-]?secret[_-]?access[_-]?key|(?:db[_-]?)?password|passwd|pwd|"
    r"database[_-]?url|private[_-]?key)[\"']?\s*[:=]\s*)"
    r"(?:(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+|"
    r"\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)",
    re.IGNORECASE,
)
_SAFE_ERROR_MAX_LENGTH = 500


class PortfolioContractError(ValueError):
    """Raised when a report cannot be treated as one complete six-account snapshot."""


@dataclass(frozen=True)
class CanonicalMoney:
    """One internally reconciled, cent-precision account valuation."""

    total: Decimal
    position: Decimal
    cash: Decimal


def canonical_money_breakdown(
    display_name: str, report: Mapping[str, Any]
) -> CanonicalMoney:
    """Round total/position once and derive cash so every output agrees exactly."""
    try:
        total = Decimal(str(report["total_value"])).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        position = Decimal(str(report["position_value"])).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, KeyError, TypeError, ValueError) as error:
        raise PortfolioContractError(
            f"{display_name} valuation을 canonical cent로 변환할 수 없습니다"
        ) from error
    if not total.is_finite() or not position.is_finite():
        raise PortfolioContractError(f"{display_name} canonical valuation이 유한하지 않습니다")
    cash = total - position
    if min(total, position, cash) < 0:
        raise PortfolioContractError(
            f"{display_name} canonical total/position/cash가 음수입니다"
        )
    return CanonicalMoney(total=total, position=position, cash=cash)


def safe_error_message(error: BaseException | str) -> str:
    """Return a bounded error string with common credentials and IDs redacted."""
    message = str(error)
    replacements = (
        (_SENSITIVE_ASSIGNMENT_RE, r"\1[REDACTED]"),
        (_SLACK_WEBHOOK_RE, "[REDACTED_SLACK_WEBHOOK]"),
        (_SUPABASE_SECRET_RE, "[REDACTED_SUPABASE_SECRET]"),
        (_SLACK_TOKEN_RE, "[REDACTED_SLACK_TOKEN]"),
        (_DATABASE_DSN_RE, "[REDACTED_DATABASE_DSN]"),
        (_GITHUB_TOKEN_RE, "[REDACTED_GITHUB_TOKEN]"),
        (_AWS_ACCESS_KEY_RE, "[REDACTED_AWS_ACCESS_KEY]"),
        (_OPENAI_TOKEN_RE, "[REDACTED_OPENAI_TOKEN]"),
        (_JWT_RE, "[REDACTED_JWT]"),
        (_AUTH_SCHEME_RE, "Authorization [REDACTED]"),
        (_CHAIN_IDENTIFIER_RE, "[REDACTED_CHAIN_ID]"),
    )
    for pattern, replacement in replacements:
        message = pattern.sub(replacement, message)
    if len(message) > _SAFE_ERROR_MAX_LENGTH:
        message = message[:_SAFE_ERROR_MAX_LENGTH] + "…[TRUNCATED]"
    return message


def normalize_display_name(value: str) -> str:
    """Normalize a Jenkins/Slack display name without changing its identity."""
    return " ".join(value.strip().upper().split())


def validate_account_display_names(display_names: list[str] | tuple[str, ...]) -> None:
    """Require the exact, non-duplicated six-account display-name contract."""
    normalized_names = [normalize_display_name(name) for name in display_names]
    if len(normalized_names) != len(set(normalized_names)):
        raise PortfolioContractError("portfolio report 계정 표시 이름이 중복됩니다")
    actual = set(normalized_names)
    if actual != CURRENT_ACCOUNT_DISPLAY_NAMES:
        raise PortfolioContractError(
            "portfolio report는 현재 6계정 exact set이어야 합니다: "
            f"missing={sorted(CURRENT_ACCOUNT_DISPLAY_NAMES - actual)}, "
            f"unexpected={sorted(actual - CURRENT_ACCOUNT_DISPLAY_NAMES)}"
        )


def validate_complete_reports(reports: Mapping[str, Mapping[str, Any]]) -> None:
    """Validate account identity and required valuation fields before any write."""
    validate_account_display_names(list(reports))

    for display_name, report in reports.items():
        validate_report_valuation(display_name, report)


def validate_report_valuation(display_name: str, report: Mapping[str, Any]) -> None:
    """Validate one fetched account valuation without asserting the full set."""
    if "error" in report:
        raise PortfolioContractError(f"{display_name}에 collection error가 있습니다")
    missing = {
        key
        for key in ("total_value", "position_value", "cash_balance", "num_positions", "positions")
        if key not in report
    }
    if missing:
        raise PortfolioContractError(
            f"{display_name} 필수 valuation field 누락: {sorted(missing)}"
        )
    raw_money = (
        report["total_value"],
        report["position_value"],
        report["cash_balance"],
    )
    if any(isinstance(value, bool) for value in raw_money):
        raise PortfolioContractError(f"{display_name} valuation에 boolean을 사용할 수 없습니다")
    raw_position_count = report["num_positions"]
    if isinstance(raw_position_count, bool) or not isinstance(raw_position_count, int):
        raise PortfolioContractError(
            f"{display_name} num_positions는 실제 integer여야 합니다"
        )
    try:
        total, position, cash = (Decimal(str(value)) for value in raw_money)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PortfolioContractError(
            f"{display_name} valuation field type이 올바르지 않습니다"
        ) from error
    if not all(value.is_finite() for value in (total, position, cash)):
        raise PortfolioContractError(f"{display_name} valuation이 유한하지 않습니다")
    if min(total, position, cash) < 0 or raw_position_count < 0:
        raise PortfolioContractError(f"{display_name} valuation/count가 음수입니다")
    positions = report["positions"]
    if not isinstance(positions, list) or raw_position_count != len(positions):
        raise PortfolioContractError(
            f"{display_name} positions와 num_positions가 일치하지 않습니다"
        )
    if any(not isinstance(position_entry, Mapping) for position_entry in positions):
        raise PortfolioContractError(f"{display_name} position entry가 object가 아닙니다")
    if abs(total - position - cash) > Decimal("0.02"):
        raise PortfolioContractError(f"{display_name} total != position + cash")
    canonical_money_breakdown(display_name, report)
