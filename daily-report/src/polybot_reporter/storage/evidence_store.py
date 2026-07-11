"""Local, sanitized evidence ledger for complete and failed daily report runs."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import stat
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from polybot_reporter.contracts import (
    ACCOUNT_ID_BY_DISPLAY_NAME,
    PortfolioContractError,
    canonical_money_breakdown,
    normalize_display_name,
    safe_error_message,
    validate_complete_reports,
    validate_report_valuation,
)

EVIDENCE_SCHEMA_VERSION = 2
DELIVERY_CHANNELS = frozenset({"supabase", "slack"})
DELIVERY_CHANNEL_STATUSES = frozenset({"SUCCESS", "FAILED", "SKIPPED"})


class EvidenceStoreError(RuntimeError):
    """Raised when a daily evidence run cannot be persisted atomically."""


@dataclass(frozen=True)
class EvidenceWriteResult:
    run_id: str
    report_date: str
    status: str
    account_count: int
    position_count: int
    database_path: Path


class DailyEvidenceStore:
    """Persist sanitized position snapshots without wallet addresses or secrets."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        timezone_name: str | None = None,
    ) -> None:
        configured_path = path or os.getenv("DAILY_EVIDENCE_DB") or "data/daily_evidence.sqlite3"
        expanded_path = Path(configured_path).expanduser()
        self.path = Path(os.path.abspath(os.fspath(expanded_path)))
        self.timezone = ZoneInfo(timezone_name or os.getenv("REPORT_TIMEZONE") or "Asia/Seoul")

    def record_run(
        self,
        reports: Mapping[str, Mapping[str, Any]],
        *,
        expected_display_names: Sequence[str],
        failed_display_names: Sequence[str] = (),
        delivery_enabled: bool = True,
        report_date: date | None = None,
        reported_at: datetime | None = None,
    ) -> EvidenceWriteResult:
        """Record one report run and every already-fetched position in one transaction."""
        observed_at = self._reported_at(reported_at)
        resolved_date = report_date or observed_at.date()
        run_id = str(uuid.uuid4())

        expected = [self._account_id(name) for name in expected_display_names]
        if len(set(expected)) != len(expected):
            raise EvidenceStoreError("evidence run의 expected account ID가 중복됩니다")

        explicit_failures = {self._account_id(name) for name in failed_display_names}
        successful_reports: list[tuple[str, Mapping[str, Any]]] = []
        report_account_ids: set[str] = set()
        for display_name, report in reports.items():
            account_id = self._account_id(display_name)
            if account_id in report_account_ids:
                raise EvidenceStoreError(f"evidence report 계정이 중복됩니다: {account_id}")
            report_account_ids.add(account_id)
            if "error" in report:
                explicit_failures.add(account_id)
            elif account_id not in explicit_failures:
                try:
                    validate_report_valuation(display_name, report)
                except PortfolioContractError:
                    explicit_failures.add(account_id)
                else:
                    successful_reports.append((account_id, report))

        expected_set = set(expected)
        explicit_failures.update(expected_set - report_account_ids)
        unexpected = report_account_ids - expected_set
        if unexpected:
            raise EvidenceStoreError(
                "evidence report에 expected 집합 밖의 계정이 있습니다: "
                + ", ".join(sorted(unexpected))
            )

        status = (
            "COMPLETE" if not explicit_failures and report_account_ids == expected_set else "FAILED"
        )
        if status == "COMPLETE":
            try:
                validate_complete_reports(reports)
            except PortfolioContractError as error:
                raise EvidenceStoreError(
                    f"COMPLETE daily evidence 계약 검증 실패: {error}"
                ) from error
        position_count = 0
        for account_id, report in successful_reports:
            positions = report.get("positions") or []
            if not isinstance(positions, list):
                raise EvidenceStoreError(f"{account_id} positions가 list가 아닙니다")
            position_count += len(positions)

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._secure_database_file()
            with sqlite3.connect(self.path, timeout=30) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 30000")
                self._ensure_schema(connection)
                connection.execute(
                    """
                    INSERT INTO evidence_report_runs (
                        run_id, schema_version, report_date, reported_at, status,
                        expected_account_count, observed_account_count,
                        expected_account_ids_json, failed_account_ids_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        EVIDENCE_SCHEMA_VERSION,
                        resolved_date.isoformat(),
                        observed_at.isoformat(),
                        status,
                        len(expected_set),
                        len(successful_reports),
                        json.dumps(sorted(expected_set), separators=(",", ":")),
                        json.dumps(sorted(explicit_failures), separators=(",", ":")),
                    ),
                )
                initial_channel_status = (
                    "PENDING" if status == "COMPLETE" and delivery_enabled else "SKIPPED"
                )
                initial_delivery_status = (
                    "PENDING" if initial_channel_status == "PENDING" else "NOT_ATTEMPTED"
                )
                connection.execute(
                    """
                    INSERT INTO evidence_delivery_status (
                        run_id, supabase_status, slack_status, delivery_status,
                        updated_at, finalized_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        initial_channel_status,
                        initial_channel_status,
                        initial_delivery_status,
                        observed_at.isoformat(),
                        (
                            observed_at.isoformat()
                            if initial_delivery_status == "NOT_ATTEMPTED"
                            else None
                        ),
                    ),
                )
                for account_id, report in successful_reports:
                    self._insert_account(connection, run_id, account_id, report)
                    for index, position in enumerate(report.get("positions") or []):
                        if not isinstance(position, Mapping):
                            raise EvidenceStoreError(
                                f"{account_id} position #{index}가 object가 아닙니다"
                            )
                        self._insert_position(connection, run_id, account_id, index, position)
        except (OSError, sqlite3.Error, ValueError, TypeError) as error:
            raise EvidenceStoreError(
                "daily evidence SQLite 적재 실패: " + safe_error_message(error)
            ) from error

        return EvidenceWriteResult(
            run_id=run_id,
            report_date=resolved_date.isoformat(),
            status=status,
            account_count=len(successful_reports),
            position_count=position_count,
            database_path=self.path,
        )

    def mark_delivery(
        self,
        run_id: str,
        channel: str,
        status: str,
        *,
        error: BaseException | str | None = None,
    ) -> str:
        """Persist one channel outcome and return the derived final delivery status."""
        normalized_channel = channel.strip().lower()
        normalized_status = status.strip().upper()
        if normalized_channel not in DELIVERY_CHANNELS:
            raise EvidenceStoreError(f"지원하지 않는 delivery channel입니다: {channel!r}")
        if normalized_status not in DELIVERY_CHANNEL_STATUSES:
            raise EvidenceStoreError(f"지원하지 않는 delivery status입니다: {status!r}")
        safe_error = safe_error_message(error) if error is not None else None
        if normalized_status == "FAILED" and not safe_error:
            safe_error = "unspecified delivery failure"
        if normalized_status != "FAILED":
            safe_error = None

        status_column = f"{normalized_channel}_status"
        error_column = f"{normalized_channel}_error"
        timestamp_column = f"{normalized_channel}_updated_at"
        updated_at = datetime.now(timezone.utc).astimezone(self.timezone).isoformat()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._secure_database_file()
            with sqlite3.connect(self.path, timeout=30) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 30000")
                self._ensure_schema(connection)
                row = connection.execute(
                    f"SELECT {status_column} FROM evidence_delivery_status WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    raise EvidenceStoreError(f"delivery evidence run을 찾지 못했습니다: {run_id}")
                if row[0] not in {"PENDING", normalized_status}:
                    raise EvidenceStoreError(
                        f"delivery status terminal transition을 바꿀 수 없습니다: "
                        f"{row[0]} -> {normalized_status}"
                    )
                connection.execute(
                    f"""
                    UPDATE evidence_delivery_status
                    SET {status_column} = ?, {error_column} = ?,
                        {timestamp_column} = ?, updated_at = ?
                    WHERE run_id = ?
                    """,
                    (
                        normalized_status,
                        safe_error,
                        updated_at,
                        updated_at,
                        run_id,
                    ),
                )
                statuses = connection.execute(
                    """
                    SELECT supabase_status, slack_status
                    FROM evidence_delivery_status WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchone()
                if statuses is None:
                    raise EvidenceStoreError(f"delivery evidence run이 사라졌습니다: {run_id}")
                final_status = self._final_delivery_status(*statuses)
                connection.execute(
                    """
                    UPDATE evidence_delivery_status
                    SET delivery_status = ?, finalized_at = ?, updated_at = ?
                    WHERE run_id = ?
                    """,
                    (
                        final_status,
                        updated_at if final_status != "PENDING" else None,
                        updated_at,
                        run_id,
                    ),
                )
        except EvidenceStoreError:
            raise
        except (OSError, sqlite3.Error, ValueError, TypeError) as error_value:
            raise EvidenceStoreError(
                "delivery evidence SQLite 갱신 실패: "
                + safe_error_message(error_value)
            ) from error_value
        return final_status

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS evidence_report_runs (
                run_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                report_date TEXT NOT NULL,
                reported_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('COMPLETE', 'FAILED')),
                expected_account_count INTEGER NOT NULL,
                observed_account_count INTEGER NOT NULL,
                expected_account_ids_json TEXT NOT NULL,
                failed_account_ids_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS evidence_account_snapshots (
                run_id TEXT NOT NULL REFERENCES evidence_report_runs(run_id) ON DELETE CASCADE,
                account_id TEXT NOT NULL,
                total_value REAL NOT NULL,
                position_value REAL NOT NULL,
                cash_value REAL NOT NULL,
                num_positions INTEGER NOT NULL,
                PRIMARY KEY (run_id, account_id)
            );

            CREATE TABLE IF NOT EXISTS evidence_positions (
                run_id TEXT NOT NULL REFERENCES evidence_report_runs(run_id) ON DELETE CASCADE,
                account_id TEXT NOT NULL,
                position_index INTEGER NOT NULL,
                condition_id TEXT,
                asset TEXT,
                outcome TEXT,
                size REAL,
                avg_price REAL,
                current_value REAL,
                cash_pnl REAL,
                realized_pnl REAL,
                redeemable INTEGER,
                end_date TEXT,
                PRIMARY KEY (run_id, account_id, position_index)
            );

            CREATE TABLE IF NOT EXISTS evidence_delivery_status (
                run_id TEXT PRIMARY KEY
                    REFERENCES evidence_report_runs(run_id) ON DELETE CASCADE,
                supabase_status TEXT NOT NULL
                    CHECK (supabase_status IN ('PENDING', 'SUCCESS', 'FAILED', 'SKIPPED')),
                slack_status TEXT NOT NULL
                    CHECK (slack_status IN ('PENDING', 'SUCCESS', 'FAILED', 'SKIPPED')),
                delivery_status TEXT NOT NULL
                    CHECK (delivery_status IN ('PENDING', 'COMPLETE', 'FAILED', 'NOT_ATTEMPTED')),
                supabase_error TEXT,
                slack_error TEXT,
                supabase_updated_at TEXT,
                slack_updated_at TEXT,
                updated_at TEXT NOT NULL,
                finalized_at TEXT
            );

            CREATE INDEX IF NOT EXISTS evidence_runs_date_status_idx
                ON evidence_report_runs(report_date, status);
            CREATE INDEX IF NOT EXISTS evidence_positions_condition_idx
                ON evidence_positions(condition_id, account_id);
            CREATE INDEX IF NOT EXISTS evidence_delivery_final_idx
                ON evidence_delivery_status(delivery_status, updated_at);
            """
        )

    @staticmethod
    def _final_delivery_status(supabase_status: str, slack_status: str) -> str:
        statuses = {supabase_status, slack_status}
        if "FAILED" in statuses:
            return "FAILED"
        if "PENDING" in statuses:
            return "PENDING"
        if statuses == {"SUCCESS"}:
            return "COMPLETE"
        if statuses == {"SKIPPED"}:
            return "NOT_ATTEMPTED"
        return "FAILED"

    def _secure_database_file(self) -> None:
        """Create/tighten the financial evidence DB to Unix mode 0600."""
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            if self.path.is_symlink():
                raise OSError("evidence DB path가 symlink입니다")
            descriptor = os.open(self.path, flags, 0o600)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("evidence DB path가 regular file이 아닙니다")
            os.fchmod(descriptor, 0o600)
            mode = stat.S_IMODE(os.fstat(descriptor).st_mode)
            if mode != 0o600:
                raise OSError(f"evidence DB mode가 0600이 아닙니다: {mode:o}")
        except OSError as error:
            raise EvidenceStoreError(
                "daily evidence DB의 0600 권한을 강제할 수 없습니다: "
                + safe_error_message(error)
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _insert_account(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        account_id: str,
        report: Mapping[str, Any],
    ) -> None:
        money = canonical_money_breakdown(account_id, report)
        connection.execute(
            """
            INSERT INTO evidence_account_snapshots (
                run_id, account_id, total_value, position_value, cash_value, num_positions
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                account_id,
                float(money.total),
                float(money.position),
                float(money.cash),
                int(report["num_positions"]),
            ),
        )

    def _insert_position(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        account_id: str,
        index: int,
        position: Mapping[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO evidence_positions (
                run_id, account_id, position_index, condition_id, asset, outcome,
                size, avg_price, current_value, cash_pnl, realized_pnl,
                redeemable, end_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                account_id,
                index,
                self._text(position.get("conditionId")),
                self._text(position.get("asset")),
                self._text(position.get("outcome")),
                self._number(position.get("size")),
                self._number(position.get("avgPrice")),
                self._number(position.get("currentValue")),
                self._number(position.get("cashPnl")),
                self._number(position.get("realizedPnl")),
                self._boolean(position.get("redeemable")),
                self._text(position.get("endDate")),
            ),
        )

    @staticmethod
    def _number(value: Any) -> float | None:
        if value is None or value == "":
            return None
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"유한하지 않은 evidence 숫자입니다: {value!r}")
        return number

    @staticmethod
    def _boolean(value: Any) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return int(value)
        normalized = str(value).strip().lower()
        if normalized in {"true", "1", "yes"}:
            return 1
        if normalized in {"false", "0", "no"}:
            return 0
        raise ValueError(f"올바르지 않은 evidence boolean입니다: {value!r}")

    @staticmethod
    def _text(value: Any) -> str | None:
        if value is None or value == "":
            return None
        return str(value)

    @staticmethod
    def _account_id(display_name: str) -> str:
        normalized = normalize_display_name(display_name)
        account_id = ACCOUNT_ID_BY_DISPLAY_NAME.get(normalized)
        if account_id is None:
            raise EvidenceStoreError(
                f"evidence account catalog에 없는 표시 이름입니다: {display_name!r}"
            )
        return account_id

    def _reported_at(self, value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc).astimezone(self.timezone)
        if value.tzinfo is None:
            return value.replace(tzinfo=self.timezone)
        return value.astimezone(self.timezone)
