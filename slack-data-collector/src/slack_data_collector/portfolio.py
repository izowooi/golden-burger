from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


class PortfolioParseError(ValueError):
    """Raised when a Slack portfolio report cannot be parsed safely."""


@dataclass(frozen=True, slots=True)
class AlgorithmAccount:
    account_id: str
    jenkins_name: str
    algorithm_code: str
    instance_no: int | None
    sort_order: int


ALGORITHM_ACCOUNTS = (
    AlgorithmAccount("golden-apple-1", "GOLDEN-APPLE (1)", "golden-apple", 1, 1),
    AlgorithmAccount("golden-banana", "GOLDEN-BANANA", "golden-banana", None, 2),
    AlgorithmAccount("golden-cherry", "GOLDEN-CHERRY", "golden-cherry", None, 3),
    AlgorithmAccount("golden-apple-2", "GOLDEN-APPLE (2)", "golden-apple", 2, 4),
)
_ACCOUNT_BY_JENKINS_NAME = {
    account.jenkins_name: account for account in ALGORITHM_ACCOUNTS
}
_ACCOUNT_SORT_ORDER = {
    account.account_id: account.sort_order for account in ALGORITHM_ACCOUNTS
}

_REPORTED_AT_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s+기준")
_MONEY_BREAKDOWN_RE = re.compile(
    r"^\$([+-]?[\d,]+(?:\.\d+)?)\s*"
    r"\(Position:\s*\$([+-]?[\d,]+(?:\.\d+)?),\s*"
    r"Cash:\s*\$([+-]?[\d,]+(?:\.\d+)?)\)$"
)


@dataclass(frozen=True, slots=True)
class MoneyBreakdown:
    total_value: Decimal
    position_value: Decimal
    cash_value: Decimal

    def to_dict(self) -> dict[str, str]:
        return {
            "total_value": _decimal_string(self.total_value),
            "position_value": _decimal_string(self.position_value),
            "cash_value": _decimal_string(self.cash_value),
        }


@dataclass(frozen=True, slots=True)
class AlgorithmBalance:
    account_id: str
    jenkins_name: str
    algorithm_code: str
    instance_no: int | None
    balance: MoneyBreakdown

    def to_dict(self, report: PortfolioReport) -> dict[str, Any]:
        return {
            "report_date": report.report_date,
            "account_id": self.account_id,
            "jenkins_name": self.jenkins_name,
            "algorithm_code": self.algorithm_code,
            "instance_no": self.instance_no,
            **self.balance.to_dict(),
            "currency": "USD",
            "reported_at": report.reported_at,
            "source_message_ts": report.source_message_ts,
        }


@dataclass(frozen=True, slots=True)
class PortfolioReport:
    report_date: str
    reported_at: str
    source_message_ts: str
    total: MoneyBreakdown
    algorithms: tuple[AlgorithmBalance, ...]

    def total_to_dict(self) -> dict[str, Any]:
        return {
            "report_date": self.report_date,
            **self.total.to_dict(),
            "currency": "USD",
            "reported_at": self.reported_at,
            "source_message_ts": self.source_message_ts,
        }


@dataclass(frozen=True, slots=True)
class PortfolioTransformResult:
    parsed_messages: int
    unique_dates: int
    overwritten_messages: int
    earliest_date: str
    latest_date: str
    output_directory: Path
    totals_path: Path
    balances_path: Path
    accounts_path: Path
    manifest_path: Path

    def summary(self) -> dict[str, Any]:
        return {
            "parsed_messages": self.parsed_messages,
            "unique_dates": self.unique_dates,
            "overwritten_messages": self.overwritten_messages,
            "earliest_date": self.earliest_date,
            "latest_date": self.latest_date,
            "output_directory": str(self.output_directory),
        }


def transform_portfolio_reports(
    raw_messages_path: Path, output_directory: Path
) -> PortfolioTransformResult:
    reports = list(_read_reports(raw_messages_path))
    if not reports:
        raise PortfolioParseError("파싱 가능한 Polymarket 일일 리포트가 없습니다.")

    latest_by_date: dict[str, PortfolioReport] = {}
    for report in reports:
        existing = latest_by_date.get(report.report_date)
        if existing is None or Decimal(report.source_message_ts) >= Decimal(
            existing.source_message_ts
        ):
            latest_by_date[report.report_date] = report

    selected = [latest_by_date[report_date] for report_date in sorted(latest_by_date)]
    output_directory.mkdir(parents=True, exist_ok=True)
    totals_path = output_directory / "portfolio_totals.jsonl"
    balances_path = output_directory / "algorithm_balances.jsonl"
    accounts_path = output_directory / "algorithm_accounts.json"
    manifest_path = output_directory / "manifest.json"

    _write_json_lines(totals_path, (report.total_to_dict() for report in selected))
    _write_json_lines(
        balances_path,
        (
            algorithm.to_dict(report)
            for report in selected
            for algorithm in report.algorithms
        ),
    )
    accounts_path.write_text(
        json.dumps(
            [asdict(account) for account in ALGORITHM_ACCOUNTS],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    result = PortfolioTransformResult(
        parsed_messages=len(reports),
        unique_dates=len(selected),
        overwritten_messages=len(reports) - len(selected),
        earliest_date=selected[0].report_date,
        latest_date=selected[-1].report_date,
        output_directory=output_directory,
        totals_path=totals_path,
        balances_path=balances_path,
        accounts_path=accounts_path,
        manifest_path=manifest_path,
    )
    manifest_path.write_text(
        json.dumps(result.summary(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return result


def parse_portfolio_message(message: dict[str, Any]) -> PortfolioReport | None:
    if message.get("subtype") != "bot_message":
        return None
    attachments = message.get("attachments")
    if not isinstance(attachments, list) or not attachments:
        return None
    summary = attachments[0]
    if not isinstance(summary, dict) or "Polymarket 전체 포트폴리오" not in str(
        summary.get("title", "")
    ):
        return None

    source_message_ts = _required_string(message, "ts", "Slack message")
    reported_at_match = _REPORTED_AT_RE.search(str(summary.get("text", "")))
    if reported_at_match is None:
        raise PortfolioParseError(
            f"리포트 기준 시각을 찾을 수 없습니다: ts={source_message_ts}"
        )

    local_datetime = datetime.strptime(
        f"{reported_at_match.group(1)} {reported_at_match.group(2)}",
        "%Y-%m-%d %H:%M:%S",
    ).replace(tzinfo=ZoneInfo("Asia/Seoul"))
    total = _extract_breakdown(summary, "총 자산", source_message_ts)

    algorithms: list[AlgorithmBalance] = []
    for attachment in attachments[1:]:
        if not isinstance(attachment, dict):
            continue
        jenkins_name = str(attachment.get("author_name") or "").strip()
        account = _ACCOUNT_BY_JENKINS_NAME.get(jenkins_name)
        if account is None:
            raise PortfolioParseError(
                f"등록되지 않은 Jenkins 계정입니다: {jenkins_name!r}, ts={source_message_ts}"
            )
        algorithms.append(
            AlgorithmBalance(
                account_id=account.account_id,
                jenkins_name=account.jenkins_name,
                algorithm_code=account.algorithm_code,
                instance_no=account.instance_no,
                balance=_extract_breakdown(attachment, "자산 가치", source_message_ts),
            )
        )

    expected_ids = {account.account_id for account in ALGORITHM_ACCOUNTS}
    actual_ids = {algorithm.account_id for algorithm in algorithms}
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        raise PortfolioParseError(
            f"리포트의 알고리즘 계정이 불완전합니다: missing={missing}, ts={source_message_ts}"
        )

    algorithms.sort(key=lambda item: _ACCOUNT_SORT_ORDER[item.account_id])
    return PortfolioReport(
        report_date=local_datetime.date().isoformat(),
        reported_at=local_datetime.isoformat(),
        source_message_ts=source_message_ts,
        total=total,
        algorithms=tuple(algorithms),
    )


def _read_reports(path: Path) -> Iterable[PortfolioReport]:
    with path.open(encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PortfolioParseError(
                    f"{path}:{line_number}: 잘못된 JSON입니다."
                ) from exc
            message = record.get("message") if isinstance(record, dict) else None
            if not isinstance(message, dict):
                raise PortfolioParseError(
                    f"{path}:{line_number}: message 객체가 없습니다."
                )
            report = parse_portfolio_message(message)
            if report is not None:
                yield report


def _extract_breakdown(
    attachment: dict[str, Any], title_part: str, message_ts: str
) -> MoneyBreakdown:
    fields = attachment.get("fields")
    if not isinstance(fields, list):
        raise PortfolioParseError(f"Attachment fields가 없습니다: ts={message_ts}")
    for field in fields:
        if not isinstance(field, dict) or title_part not in str(field.get("title", "")):
            continue
        match = _MONEY_BREAKDOWN_RE.match(str(field.get("value", "")).strip())
        if match is None:
            raise PortfolioParseError(
                f"잔고 형식을 파싱할 수 없습니다: ts={message_ts}"
            )
        try:
            values = tuple(Decimal(value.replace(",", "")) for value in match.groups())
        except InvalidOperation as exc:
            raise PortfolioParseError(f"잘못된 금액입니다: ts={message_ts}") from exc
        return MoneyBreakdown(*values)
    raise PortfolioParseError(
        f"{title_part!r} 필드를 찾을 수 없습니다: ts={message_ts}"
    )


def _required_string(value: dict[str, Any], key: str, context: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise PortfolioParseError(f"{context}의 {key} 값이 없습니다.")
    return result


def _decimal_string(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def _write_json_lines(path: Path, records: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(
                json.dumps(
                    record, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                )
                + "\n"
            )
