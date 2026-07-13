from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class PortfolioParseError(ValueError):
    """Raised when a Slack portfolio report cannot be parsed safely."""


LEGACY_REPORT_SCHEMA_VERSION = "pb-portfolio/v1"
PREVIOUS_REPORT_SCHEMA_VERSION = "pb-portfolio/v2"
CURRENT_REPORT_SCHEMA_VERSION = "pb-portfolio/v3"
ERROR_REPORT_SCHEMA_VERSION = "pb-portfolio/error-v1"


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
    AlgorithmAccount("golden-eco", "GOLDEN-ECO", "golden-honeydew", None, 5),
    AlgorithmAccount("golden-fox", "GOLDEN-FOX", "golden-nectarine", None, 6),
    AlgorithmAccount("golden-lion", "GOLDEN-LION", "golden-lion", None, 7),
    AlgorithmAccount("golden-tiger", "GOLDEN-TIGER", "golden-tiger", None, 8),
    AlgorithmAccount("golden-wolf", "GOLDEN-WOLF", "golden-wolf", None, 9),
)
_ACCOUNT_BY_JENKINS_NAME = {
    account.jenkins_name: account for account in ALGORITHM_ACCOUNTS
}
_ACCOUNT_SORT_ORDER = {
    account.account_id: account.sort_order for account in ALGORITHM_ACCOUNTS
}
_LEGACY_ACCOUNT_IDS = {
    "golden-apple-1",
    "golden-banana",
    "golden-cherry",
    "golden-apple-2",
}
_PREVIOUS_ACCOUNT_IDS = {
    "golden-apple-1",
    "golden-banana",
    "golden-cherry",
    "golden-apple-2",
    "golden-eco",
    "golden-fox",
}
_CURRENT_ACCOUNT_IDS = {account.account_id for account in ALGORITHM_ACCOUNTS}

_REPORTED_AT_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s+기준")
_SCHEMA_VERSION_RE = re.compile(r"\bpb-portfolio/v\d+\b", re.IGNORECASE)
_REPORT_STATUS_RE = re.compile(
    r"\b(STARTED|COMPLETE|FAILED|INCOMPLETE|ERROR)\b", re.IGNORECASE
)
_MESSAGE_MARKER_BY_SCHEMA = {
    version: re.compile(rf"\[{re.escape(version)}\s+COMPLETE\]", re.IGNORECASE)
    for version in (PREVIOUS_REPORT_SCHEMA_VERSION, CURRENT_REPORT_SCHEMA_VERSION)
}
_FOOTER_MARKER_BY_SCHEMA = {
    version: re.compile(
        rf"(?:^|•\s*){re.escape(version)}\s*•\s*COMPLETE(?:\s*•|$)",
        re.IGNORECASE,
    )
    for version in (PREVIOUS_REPORT_SCHEMA_VERSION, CURRENT_REPORT_SCHEMA_VERSION)
}
_TIMEZONE_RE = re.compile(r"\btz=([A-Za-z0-9_+\-/]+)")
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
            "source_schema_version": report.schema_version,
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
    schema_version: str
    report_date: str
    reported_at: str
    source_message_ts: str
    total: MoneyBreakdown
    algorithms: tuple[AlgorithmBalance, ...]

    def total_to_dict(self) -> dict[str, Any]:
        return {
            "source_schema_version": self.schema_version,
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
    if _is_error_report(message, attachments):
        raise PortfolioParseError(
            f"오류 상태의 Polymarket 리포트는 적재할 수 없습니다: ts={message.get('ts')}"
        )
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

    timezone_name = _report_timezone(summary)
    try:
        report_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise PortfolioParseError(
            f"지원하지 않는 리포트 timezone입니다: {timezone_name!r}, ts={source_message_ts}"
        ) from exc
    local_datetime = datetime.strptime(
        f"{reported_at_match.group(1)} {reported_at_match.group(2)}",
        "%Y-%m-%d %H:%M:%S",
    ).replace(tzinfo=report_timezone)
    total = _extract_breakdown(summary, "총 자산", source_message_ts)

    account_attachments = [item for item in attachments[1:] if isinstance(item, dict)]
    styles = {
        _account_attachment_style(item, source_message_ts)
        for item in account_attachments
    }
    if len(styles) != 1:
        raise PortfolioParseError(
            f"legacy fields와 current text 계정 attachment를 섞을 수 없습니다: "
            f"ts={source_message_ts}"
        )
    style = next(iter(styles), None)
    explicit_schema = _explicit_schema_version(message, summary, source_message_ts)
    inferred_schema = (
        LEGACY_REPORT_SCHEMA_VERSION
        if style == "fields"
        else PREVIOUS_REPORT_SCHEMA_VERSION
    )
    allowed_for_style = (
        {LEGACY_REPORT_SCHEMA_VERSION}
        if style == "fields"
        else {PREVIOUS_REPORT_SCHEMA_VERSION, CURRENT_REPORT_SCHEMA_VERSION}
    )
    if explicit_schema is not None and explicit_schema not in allowed_for_style:
        raise PortfolioParseError(
            f"리포트 schema와 attachment 형식이 일치하지 않습니다: "
            f"schema={explicit_schema}, style={style}, ts={source_message_ts}"
        )
    schema_version = explicit_schema or inferred_schema
    if style == "text":
        _validate_text_report_markers(
            message,
            summary,
            schema_version,
            source_message_ts,
        )
        _validate_current_payload_status(message, source_message_ts)

    algorithms: list[AlgorithmBalance] = []
    seen_account_ids: set[str] = set()
    for attachment in account_attachments:
        jenkins_name = " ".join(
            str(attachment.get("author_name") or "").strip().upper().split()
        )
        account = _ACCOUNT_BY_JENKINS_NAME.get(jenkins_name)
        if account is None:
            raise PortfolioParseError(
                f"등록되지 않은 Jenkins 계정입니다: {jenkins_name!r}, ts={source_message_ts}"
            )
        if account.account_id in seen_account_ids:
            raise PortfolioParseError(
                f"리포트에 동일한 Jenkins 계정이 중복되었습니다: "
                f"{jenkins_name!r}, ts={source_message_ts}"
            )
        seen_account_ids.add(account.account_id)
        algorithms.append(
            AlgorithmBalance(
                account_id=account.account_id,
                jenkins_name=account.jenkins_name,
                algorithm_code=account.algorithm_code,
                instance_no=account.instance_no,
                balance=_extract_account_breakdown(
                    attachment, style, source_message_ts
                ),
            )
        )

    expected_ids = {
        LEGACY_REPORT_SCHEMA_VERSION: _LEGACY_ACCOUNT_IDS,
        PREVIOUS_REPORT_SCHEMA_VERSION: _PREVIOUS_ACCOUNT_IDS,
        CURRENT_REPORT_SCHEMA_VERSION: _CURRENT_ACCOUNT_IDS,
    }[schema_version]
    actual_ids = {algorithm.account_id for algorithm in algorithms}
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        unexpected = sorted(actual_ids - expected_ids)
        raise PortfolioParseError(
            "리포트의 알고리즘 계정 집합이 허용된 legacy4/v2-six/v3-nine "
            "계약과 다릅니다: "
            f"missing={missing}, unexpected={unexpected}, ts={source_message_ts}"
        )

    _validate_portfolio_reconciliation(total, algorithms, source_message_ts)

    algorithms.sort(key=lambda item: _ACCOUNT_SORT_ORDER[item.account_id])
    return PortfolioReport(
        schema_version=schema_version,
        report_date=local_datetime.date().isoformat(),
        reported_at=local_datetime.isoformat(),
        source_message_ts=source_message_ts,
        total=total,
        algorithms=tuple(algorithms),
    )


def _is_error_report(message: dict[str, Any], attachments: list[Any]) -> bool:
    fragments = [str(message.get("text") or "")]
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        fragments.extend(
            str(attachment.get(key) or "") for key in ("title", "text", "footer")
        )
    combined = "\n".join(fragments)
    return (
        ERROR_REPORT_SCHEMA_VERSION in combined
        or "Polymarket Bot Error" in combined
        or "Error in Daily Report" in combined
    )


def _explicit_schema_version(
    message: dict[str, Any], summary: dict[str, Any], message_ts: str
) -> str | None:
    text = "\n".join((str(message.get("text") or ""), str(summary.get("footer") or "")))
    versions = {match.lower() for match in _SCHEMA_VERSION_RE.findall(text)}
    if len(versions) > 1:
        raise PortfolioParseError(
            f"서로 다른 리포트 schema marker가 있습니다: {sorted(versions)}, ts={message_ts}"
        )
    if not versions:
        return None
    version = versions.pop()
    if version not in {
        LEGACY_REPORT_SCHEMA_VERSION,
        PREVIOUS_REPORT_SCHEMA_VERSION,
        CURRENT_REPORT_SCHEMA_VERSION,
    }:
        raise PortfolioParseError(
            f"지원하지 않는 리포트 schema입니다: {version}, ts={message_ts}"
        )
    return version


def _validate_text_report_markers(
    message: dict[str, Any],
    summary: dict[str, Any],
    schema_version: str,
    message_ts: str,
) -> None:
    """Require redundant, unambiguous COMPLETE attestations."""
    locations = {
        "message text": (
            str(message.get("text") or ""),
            _MESSAGE_MARKER_BY_SCHEMA[schema_version],
        ),
        "summary footer": (
            str(summary.get("footer") or ""),
            _FOOTER_MARKER_BY_SCHEMA[schema_version],
        ),
    }
    for location, (value, marker_pattern) in locations.items():
        versions = {match.lower() for match in _SCHEMA_VERSION_RE.findall(value)}
        statuses = {match.upper() for match in _REPORT_STATUS_RE.findall(value)}
        if (
            versions != {schema_version}
            or statuses != {"COMPLETE"}
            or marker_pattern.search(value) is None
        ):
            raise PortfolioParseError(
                "current report는 message text와 summary footer 각각에 "
                f"{schema_version} + COMPLETE만 명시해야 합니다: "
                f"location={location}, schema={sorted(versions)}, "
                f"status={sorted(statuses)}, ts={message_ts}"
            )
    if _TIMEZONE_RE.search(str(summary.get("footer") or "")) is None:
        raise PortfolioParseError(
            f"current report summary footer에 tz=<IANA timezone>이 없습니다: "
            f"ts={message_ts}"
        )


def _validate_current_payload_status(message: dict[str, Any], message_ts: str) -> None:
    """Reject a COMPLETE marker contradicted anywhere in the Slack payload."""
    statuses = {
        match.upper()
        for fragment in _iter_payload_strings(message)
        for match in _REPORT_STATUS_RE.findall(fragment)
    }
    if statuses != {"COMPLETE"}:
        raise PortfolioParseError(
            "current report 전체 payload에는 모순 없는 COMPLETE status만 있어야 합니다: "
            f"status={sorted(statuses)}, ts={message_ts}"
        )


def _iter_payload_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _iter_payload_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_payload_strings(child)


def _report_timezone(summary: dict[str, Any]) -> str:
    match = _TIMEZONE_RE.search(str(summary.get("footer") or ""))
    return match.group(1) if match else "Asia/Seoul"


def _account_attachment_style(attachment: dict[str, Any], message_ts: str) -> str:
    fields = attachment.get("fields")
    text = str(attachment.get("text") or "")
    has_fields = isinstance(fields, list)
    has_text_breakdown = any(
        _MONEY_BREAKDOWN_RE.match(line.strip()) for line in text.splitlines()
    )
    if has_fields and has_text_breakdown:
        raise PortfolioParseError(
            f"계정 attachment에 fields와 text 잔고가 동시에 있습니다: ts={message_ts}"
        )
    if has_fields:
        return "fields"
    if has_text_breakdown:
        return "text"
    raise PortfolioParseError(f"계정 attachment 잔고 형식이 없습니다: ts={message_ts}")


def _extract_account_breakdown(
    attachment: dict[str, Any], style: str | None, message_ts: str
) -> MoneyBreakdown:
    if style == "fields":
        return _extract_breakdown(attachment, "자산 가치", message_ts)
    if style == "text":
        for line in str(attachment.get("text") or "").splitlines():
            match = _MONEY_BREAKDOWN_RE.match(line.strip())
            if match is not None:
                return _money_breakdown_from_match(match, message_ts)
    raise PortfolioParseError(f"계정 잔고를 파싱할 수 없습니다: ts={message_ts}")


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
        return _money_breakdown_from_match(match, message_ts)
    raise PortfolioParseError(
        f"{title_part!r} 필드를 찾을 수 없습니다: ts={message_ts}"
    )


def _money_breakdown_from_match(
    match: re.Match[str], message_ts: str
) -> MoneyBreakdown:
    try:
        values = tuple(Decimal(value.replace(",", "")) for value in match.groups())
    except InvalidOperation as exc:
        raise PortfolioParseError(f"잘못된 금액입니다: ts={message_ts}") from exc
    total, position, cash = values
    if min(values) < 0:
        raise PortfolioParseError(f"잔고 금액은 음수일 수 없습니다: ts={message_ts}")
    if abs(total - position - cash) > Decimal("0.02"):
        raise PortfolioParseError(
            f"잔고 total이 position + cash와 일치하지 않습니다: ts={message_ts}"
        )
    canonical_cash = total - position
    if canonical_cash < 0:
        raise PortfolioParseError(
            f"canonical cash가 음수입니다: ts={message_ts}"
        )
    return MoneyBreakdown(total, position, canonical_cash)


def _validate_portfolio_reconciliation(
    total: MoneyBreakdown,
    algorithms: list[AlgorithmBalance],
    message_ts: str,
) -> None:
    account_sums = MoneyBreakdown(
        total_value=sum(
            (algorithm.balance.total_value for algorithm in algorithms), Decimal("0")
        ),
        position_value=sum(
            (algorithm.balance.position_value for algorithm in algorithms), Decimal("0")
        ),
        cash_value=sum(
            (algorithm.balance.cash_value for algorithm in algorithms), Decimal("0")
        ),
    )
    mismatched = [
        field
        for field in ("total_value", "position_value", "cash_value")
        if abs(getattr(total, field) - getattr(account_sums, field)) > Decimal("0.05")
    ]
    if mismatched:
        raise PortfolioParseError(
            "전체 summary와 계정 합계가 일치하지 않습니다: "
            f"fields={mismatched}, ts={message_ts}"
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
