"""Store daily portfolio snapshots in Supabase."""

from __future__ import annotations

import base64
import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from supabase import create_client


class SupabaseConfigurationError(ValueError):
    """Raised when required Supabase configuration is missing."""


class SupabaseWriteError(RuntimeError):
    """Raised when a snapshot cannot be safely persisted."""


@dataclass(frozen=True)
class SnapshotWriteResult:
    """Summary of an inserted or updated daily snapshot."""

    report_date: str
    account_count: int
    total_value: float
    position_value: float
    cash_value: float


class SupabasePortfolioWriter:
    """Write complete, date-keyed portfolio snapshots to the existing pb_* tables."""

    ACCOUNT_TABLE = "pb_algorithm_accounts"
    BALANCE_TABLE = "pb_daily_algorithm_balances"
    TOTAL_TABLE = "pb_daily_portfolio_totals"

    def __init__(
        self,
        url: str | None = None,
        secret_key: str | None = None,
        *,
        client: Any | None = None,
        timezone: str | None = None,
    ) -> None:
        timezone_name = timezone or os.getenv("REPORT_TIMEZONE") or "Asia/Seoul"
        self.timezone = ZoneInfo(timezone_name)

        if client is not None:
            self.client = client
            return

        resolved_url = url or os.getenv("SUPABASE_URL")
        resolved_key = secret_key or os.getenv("SUPABASE_SECRET_KEY")
        if not resolved_url or not resolved_key:
            missing = [
                name
                for name, value in (
                    ("SUPABASE_URL", resolved_url),
                    ("SUPABASE_SECRET_KEY", resolved_key),
                )
                if not value
            ]
            raise SupabaseConfigurationError(
                f"필수 Supabase 환경변수가 없습니다: {', '.join(missing)}"
            )

        validated_key = self._validate_server_key(resolved_key)
        self.client = create_client(resolved_url, validated_key)

    def check_connection(self) -> int:
        """Verify server credentials by reading the account catalog."""
        return len(self._load_account_catalog())

    def write_daily_snapshot(
        self,
        reports: Mapping[str, Mapping[str, Any]],
        *,
        report_date: date | str | None = None,
        reported_at: datetime | None = None,
    ) -> SnapshotWriteResult:
        """Upsert one complete daily snapshot.

        Every account in ``pb_algorithm_accounts`` must be present. This prevents a
        failed or incomplete collection from replacing a valid daily snapshot.
        """
        if not reports:
            raise SupabaseWriteError("저장할 계정 리포트가 없습니다")

        failed_accounts = [name for name, report in reports.items() if report.get("error")]
        if failed_accounts:
            raise SupabaseWriteError(
                "수집 실패 계정이 있어 DB 적재를 중단합니다: " + ", ".join(failed_accounts)
            )

        snapshot_time = self._normalize_reported_at(reported_at)
        snapshot_date = self._normalize_report_date(report_date, snapshot_time)
        report_date_text = snapshot_date.isoformat()
        reported_at_text = snapshot_time.isoformat()
        source_message_ts = round(snapshot_time.timestamp(), 6)

        catalog = self._load_account_catalog()
        catalog_by_name = {
            self._normalize_jenkins_name(row["jenkins_name"]): row["account_id"] for row in catalog
        }
        expected_account_ids = set(catalog_by_name.values())

        balance_rows: list[dict[str, Any]] = []
        mapped_account_ids: set[str] = set()
        raw_totals = {"total": Decimal("0"), "position": Decimal("0"), "cash": Decimal("0")}

        for report_name, report in reports.items():
            account_id = catalog_by_name.get(self._normalize_jenkins_name(report_name))
            if not account_id:
                raise SupabaseWriteError(
                    f"DB 계정 카탈로그에서 Jenkins 이름을 찾지 못했습니다: {report_name}"
                )
            if account_id in mapped_account_ids:
                raise SupabaseWriteError(f"동일한 DB 계정이 중복 매핑되었습니다: {account_id}")

            raw_total = self._decimal_value(report.get("total_value"), report_name, "total_value")
            raw_position = self._decimal_value(
                report.get("position_value"), report_name, "position_value"
            )
            raw_cash = self._decimal_value(report.get("cash_balance"), report_name, "cash_balance")
            if abs(raw_total - raw_position - raw_cash) > Decimal("0.02"):
                raise SupabaseWriteError(
                    f"{report_name} 총액이 포지션과 현금의 합계와 일치하지 않습니다"
                )

            mapped_account_ids.add(account_id)
            raw_totals["total"] += raw_total
            raw_totals["position"] += raw_position
            raw_totals["cash"] += raw_cash
            balance_rows.append(
                {
                    "report_date": report_date_text,
                    "account_id": account_id,
                    "total_value": self._money(raw_total),
                    "position_value": self._money(raw_position),
                    "cash_value": self._money(raw_cash),
                    "currency": "USD",
                    "reported_at": reported_at_text,
                    "source_message_ts": source_message_ts,
                    "updated_at": reported_at_text,
                }
            )

        missing_account_ids = expected_account_ids - mapped_account_ids
        if missing_account_ids:
            raise SupabaseWriteError(
                "일부 DB 계정의 리포트가 없어 적재를 중단합니다: "
                + ", ".join(sorted(missing_account_ids))
            )

        total_value = self._money(raw_totals["total"])
        position_value = self._money(raw_totals["position"])
        cash_value = self._money(raw_totals["cash"])
        total_row: dict[str, Any] = {
            "report_date": report_date_text,
            "total_value": total_value,
            "position_value": position_value,
            "cash_value": cash_value,
            "currency": "USD",
            "reported_at": reported_at_text,
            "source_message_ts": source_message_ts,
            "updated_at": reported_at_text,
        }

        try:
            balance_response = (
                self.client.table(self.BALANCE_TABLE)
                .upsert(balance_rows, on_conflict="report_date,account_id")
                .select("report_date,account_id")
                .execute()
            )
            if len(balance_response.data or []) != len(balance_rows):
                raise SupabaseWriteError("계정별 잔고 upsert 결과 행 수가 예상과 다릅니다")

            total_response = (
                self.client.table(self.TOTAL_TABLE)
                .upsert(total_row, on_conflict="report_date")
                .select("report_date")
                .execute()
            )
            if len(total_response.data or []) != 1:
                raise SupabaseWriteError("전체 잔고 upsert 결과 행 수가 예상과 다릅니다")
        except SupabaseWriteError:
            raise
        except Exception as exc:
            raise SupabaseWriteError(f"Supabase upsert 실패: {exc}") from exc

        return SnapshotWriteResult(
            report_date=report_date_text,
            account_count=len(balance_rows),
            total_value=total_value,
            position_value=position_value,
            cash_value=cash_value,
        )

    def get_period_pnl(
        self,
        reports: Mapping[str, Mapping[str, Any]],
        *,
        windows: Sequence[int] = (7, 30),
        as_of: date | None = None,
    ) -> dict[str, dict[int, float | None]]:
        """Period P&L per account = current total_value − baseline total_value.

        ``baseline(window)`` is the total_value of the earliest stored snapshot
        with ``(as_of - (window - 1)) <= report_date < as_of`` for that account.
        This mirrors the dashboard definition (change of total between the first
        and last day of the window) so both surfaces agree. Deposits/withdrawals
        are not adjusted, same as the dashboard.

        Returns ``{report_name: {window: pnl_or_None}}``; ``None`` means there is
        no snapshot old enough to anchor that window.
        """
        if not reports or not windows:
            return {}
        resolved_as_of = as_of or datetime.now(self.timezone).date()

        catalog_by_name = {
            self._normalize_jenkins_name(row["jenkins_name"]): row["account_id"]
            for row in self._load_account_catalog()
        }

        name_to_account: dict[str, str] = {}
        current_totals: dict[str, float] = {}
        for report_name, report in reports.items():
            if report.get("error"):
                continue
            account_id = catalog_by_name.get(self._normalize_jenkins_name(report_name))
            if not account_id:
                continue
            try:
                current_totals[account_id] = float(report.get("total_value"))
            except (TypeError, ValueError):
                continue
            name_to_account[report_name] = account_id

        if not current_totals:
            return {}

        since = (resolved_as_of - timedelta(days=max(windows) - 1)).isoformat()
        try:
            response = (
                self.client.table(self.BALANCE_TABLE)
                .select("report_date,account_id,total_value")
                .in_("account_id", list(current_totals.keys()))
                .gte("report_date", since)
                .lt("report_date", resolved_as_of.isoformat())
                .execute()
            )
        except Exception as exc:
            raise SupabaseWriteError(f"기간 손익용 과거 스냅샷 조회 실패: {exc}") from exc

        by_account = self._compute_period_pnl(
            current_totals, response.data or [], resolved_as_of, windows
        )
        return {
            report_name: by_account.get(account_id, {})
            for report_name, account_id in name_to_account.items()
        }

    @staticmethod
    def _compute_period_pnl(
        current_totals: Mapping[str, float],
        history_rows: Sequence[Mapping[str, Any]],
        as_of: date,
        windows: Sequence[int],
    ) -> dict[str, dict[int, float | None]]:
        """Pure period-P&L logic; see :meth:`get_period_pnl`.

        Rows on or after ``as_of`` are ignored so the live in-memory total is the
        only "current" value. For each window the baseline is the earliest row
        within ``[as_of - (window - 1), as_of)``.
        """
        as_of_text = as_of.isoformat()
        by_account: dict[str, list[tuple[str, float]]] = {}
        for row in history_rows:
            report_date = str(row.get("report_date"))
            if report_date >= as_of_text:
                continue
            try:
                total = float(row.get("total_value"))
            except (TypeError, ValueError):
                continue
            by_account.setdefault(str(row.get("account_id")), []).append((report_date, total))

        result: dict[str, dict[int, float | None]] = {}
        for account_id, current_total in current_totals.items():
            rows = sorted(by_account.get(account_id, []))
            per_window: dict[int, float | None] = {}
            for window in windows:
                floor = (as_of - timedelta(days=window - 1)).isoformat()
                baseline = next((total for report_date, total in rows if report_date >= floor), None)
                per_window[window] = None if baseline is None else round(current_total - baseline, 6)
            result[account_id] = per_window
        return result

    def _load_account_catalog(self) -> list[dict[str, str]]:
        try:
            response = (
                self.client.table(self.ACCOUNT_TABLE).select("account_id,jenkins_name").execute()
            )
        except Exception as exc:
            if self._api_error_code(exc) == "42501":
                raise SupabaseWriteError(
                    "pb_algorithm_accounts 조회 권한이 없습니다. Jenkins의 "
                    "SUPABASE_SECRET_KEY에 sb_secret_... 형식의 서버 전용 Secret key를 "
                    "설정하세요. sb_publishable_... 또는 anon key에 DB 권한을 추가하면 안 됩니다."
                ) from exc
            raise SupabaseWriteError(f"Supabase 계정 카탈로그 조회 실패: {exc}") from exc

        rows = response.data or []
        if not rows:
            raise SupabaseWriteError("Supabase 계정 카탈로그가 비어 있습니다")
        return rows

    def _normalize_reported_at(self, value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(self.timezone)
        if value.tzinfo is None:
            return value.replace(tzinfo=self.timezone)
        return value.astimezone(self.timezone)

    @staticmethod
    def _normalize_report_date(value: date | str | None, snapshot_time: datetime) -> date:
        if value is None:
            return snapshot_time.date()
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise SupabaseWriteError("report_date는 YYYY-MM-DD 형식이어야 합니다") from exc

    @staticmethod
    def _normalize_jenkins_name(value: str) -> str:
        return " ".join(value.strip().upper().split())

    @classmethod
    def _validate_server_key(cls, value: str) -> str:
        key = value.strip()
        if key.startswith("sb_publishable_"):
            raise SupabaseConfigurationError(
                "SUPABASE_SECRET_KEY에 sb_publishable_... 키가 설정되었습니다. "
                "Jenkins DB 적재에는 Supabase Dashboard의 Settings → API Keys → "
                "Secret keys에서 발급한 sb_secret_... 서버 전용 키가 필요합니다."
            )
        if key.startswith("sb_secret_"):
            if "replace_with" in key or "your_server" in key:
                raise SupabaseConfigurationError(
                    "SUPABASE_SECRET_KEY가 예제 placeholder입니다. 실제 sb_secret_... 키를 설정하세요."
                )
            return key

        legacy_role = cls._legacy_jwt_role(key)
        if legacy_role == "service_role":
            return key
        if legacy_role == "anon":
            raise SupabaseConfigurationError(
                "SUPABASE_SECRET_KEY에 legacy anon key가 설정되었습니다. "
                "sb_secret_... 서버 전용 Secret key를 설정하세요."
            )
        raise SupabaseConfigurationError(
            "SUPABASE_SECRET_KEY가 서버 전용 키 형식이 아닙니다. "
            "sb_secret_... Secret key를 설정하세요."
        )

    @staticmethod
    def _legacy_jwt_role(value: str) -> str | None:
        parts = value.split(".")
        if len(parts) != 3:
            return None
        try:
            padding = "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + padding))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        role = payload.get("role")
        return role if isinstance(role, str) else None

    @staticmethod
    def _api_error_code(exc: Exception) -> str | None:
        code = getattr(exc, "code", None)
        if isinstance(code, str):
            return code
        if exc.args and isinstance(exc.args[0], dict):
            value = exc.args[0].get("code")
            return value if isinstance(value, str) else None
        return None

    @staticmethod
    def _decimal_value(value: Any, account_name: str, field_name: str) -> Decimal:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise SupabaseWriteError(
                f"{account_name}의 {field_name} 값이 올바르지 않습니다: {value!r}"
            ) from exc
        if not math.isfinite(float(number)):
            raise SupabaseWriteError(f"{account_name}의 {field_name} 값이 유한한 숫자가 아닙니다")
        return number

    @staticmethod
    def _money(value: Decimal) -> float:
        return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
