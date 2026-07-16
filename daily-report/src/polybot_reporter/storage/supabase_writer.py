"""Store daily portfolio snapshots in Supabase."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from supabase import create_client

from polybot_reporter.contracts import (
    PORTFOLIO_REPORT_SCHEMA_VERSION,
    PortfolioContractError,
    canonical_money_breakdown,
    normalize_display_name,
    safe_error_message,
    stable_account_id,
    validate_account_display_names,
    validate_complete_reports,
)


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


@dataclass(frozen=True)
class CatalogSyncResult:
    """Summary of an explicit, add-only account catalog synchronization."""

    requested_count: int
    inserted_count: int
    catalog_count: int


class SupabasePortfolioWriter:
    """Write complete date-keyed snapshots through one transactional DB RPC."""

    ACCOUNT_TABLE = "pb_algorithm_accounts"
    BALANCE_TABLE = "pb_daily_algorithm_balances"
    TOTAL_TABLE = "pb_daily_portfolio_totals"
    PREFLIGHT_RPC = "pb_portfolio_writer_preflight_v3"
    SNAPSHOT_RPC = "pb_write_complete_portfolio_snapshot_v3"
    CATALOG_SYNC_RPC = "pb_register_algorithm_accounts_v1"

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
        try:
            self.client = create_client(resolved_url, validated_key)
        except Exception as exc:
            raise SupabaseConfigurationError(
                "Supabase client 초기화 실패: " + safe_error_message(exc)
            ) from exc

    def check_connection(self, configured_names: Sequence[str]) -> int:
        """Verify credentials and the exact Jenkins-env/catalog account contract.

        A count-only health check can pass while the configured wallets belong to a
        different or incomplete set of display names. Requiring the exact normalized
        set makes ``check-supabase`` a deployment preflight for the snapshot writer.
        """
        try:
            validate_account_display_names(list(configured_names))
        except PortfolioContractError as error:
            raise SupabaseWriteError(
                f"Jenkins 계정 설정 계약 불일치: {error}"
            ) from error
        catalog = self._validate_catalog(self._load_account_catalog())
        configured = {normalize_display_name(name) for name in configured_names}
        catalog_names = {row["jenkins_name"] for row in catalog}
        if configured != catalog_names:
            raise SupabaseWriteError(
                "Jenkins 계정과 Supabase 카탈로그가 일치하지 않습니다: "
                f"missing={sorted(catalog_names - configured)}, "
                f"unexpected={sorted(configured - catalog_names)}"
            )
        try:
            response = self.client.rpc(self.PREFLIGHT_RPC, {}).execute()
        except Exception as exc:
            if self._api_error_code(exc) == "PGRST202":
                raise SupabaseWriteError(
                    "Supabase atomic snapshot RPC preflight 실패(PGRST202). "
                    "운영 DB에 함수가 없거나 PostgREST schema cache가 오래된 상태입니다. "
                    "daily-report/SUPABASE_MIGRATION.md의 진단 절차를 실행하세요. "
                    "함수가 없으면 slack-data-collector/sql/pb_portfolio_history_v3.sql을 "
                    "적용하고, 함수가 있으면 NOTIFY pgrst, 'reload schema'를 실행한 뒤 "
                    "check-supabase를 다시 실행하세요. unsafe fallback은 없습니다."
                ) from exc
            raise SupabaseWriteError(
                "Supabase atomic snapshot RPC preflight 실패. "
                "slack-data-collector/sql/pb_portfolio_history_v3.sql migration을 "
                f"먼저 적용하세요: {safe_error_message(exc)}"
            ) from exc
        payload = self._rpc_object(response.data, "atomic snapshot RPC preflight")
        if (
            payload.get("contract_version") != PORTFOLIO_REPORT_SCHEMA_VERSION
            or payload.get("account_count") != len(catalog)
        ):
            raise SupabaseWriteError(
                "Supabase atomic snapshot RPC contract 응답이 예상과 다릅니다"
            )
        return len(catalog)

    def sync_catalog(self, configured_names: Sequence[str]) -> CatalogSyncResult:
        """Register Jenkins accounts missing from the catalog, without mutations.

        This is deliberately separate from the daily report path. Existing rows are
        never renamed, reordered, overwritten, or deleted; those changes require an
        operator to review the catalog directly.
        """
        try:
            validate_account_display_names(list(configured_names))
        except PortfolioContractError as error:
            raise SupabaseWriteError(
                f"Jenkins 계정 설정 계약 불일치: {error}"
            ) from error

        catalog = self._validate_catalog(self._load_account_catalog())
        catalog_by_name = {row["jenkins_name"]: row["account_id"] for row in catalog}
        configured = [normalize_display_name(name) for name in configured_names]
        configured_set = set(configured)
        catalog_names = set(catalog_by_name)
        removed_from_jenkins = catalog_names - configured_set
        if removed_from_jenkins:
            raise SupabaseWriteError(
                "카탈로그 동기화는 계정을 삭제하지 않습니다. Jenkins에서 사라진 계정을 "
                f"Supabase Console에서 먼저 검토하세요: {sorted(removed_from_jenkins)}"
            )

        existing_ids = set(catalog_by_name.values())
        accounts: list[dict[str, Any]] = []
        for sort_order, display_name in enumerate(configured, start=1):
            if display_name in catalog_by_name:
                continue
            account_id = stable_account_id(display_name)
            if account_id in existing_ids:
                raise SupabaseWriteError(
                    "신규 Jenkins 계정의 stable ID가 기존 Supabase 계정과 충돌합니다: "
                    f"{display_name} -> {account_id}"
                )
            instance_no = None
            algorithm_code = account_id
            duplicate = display_name.rsplit(" (", 1)
            if len(duplicate) == 2 and duplicate[1].endswith(")"):
                suffix = duplicate[1][:-1]
                if suffix.isdigit() and int(suffix) > 0:
                    instance_no = int(suffix)
                    algorithm_code = stable_account_id(duplicate[0])
            accounts.append(
                {
                    "account_id": account_id,
                    "jenkins_name": display_name,
                    "algorithm_code": algorithm_code,
                    "instance_no": instance_no,
                    "sort_order": sort_order,
                }
            )
            existing_ids.add(account_id)

        if not accounts:
            account_count = self.check_connection(configured_names)
            return CatalogSyncResult(0, 0, account_count)

        try:
            response = self.client.rpc(
                self.CATALOG_SYNC_RPC, {"p_accounts": accounts}
            ).execute()
        except Exception as exc:
            if self._api_error_code(exc) == "PGRST202":
                raise SupabaseWriteError(
                    "Supabase 카탈로그 동기화 RPC가 없습니다. "
                    "slack-data-collector/sql/pb_algorithm_account_catalog_sync_v1.sql "
                    "migration을 먼저 적용하세요."
                ) from exc
            raise SupabaseWriteError(
                "Supabase 계정 카탈로그 동기화 실패: " + safe_error_message(exc)
            ) from exc

        payload = self._rpc_object(response.data, "account catalog sync RPC")
        try:
            requested_count = int(payload.get("requested_count"))
            inserted_count = int(payload.get("inserted_count"))
            catalog_count = int(payload.get("catalog_count"))
        except (TypeError, ValueError) as error:
            raise SupabaseWriteError(
                "Supabase 계정 카탈로그 동기화 응답이 불완전합니다"
            ) from error
        if requested_count != len(accounts) or inserted_count < 0:
            raise SupabaseWriteError(
                "Supabase 계정 카탈로그 동기화 대사 결과가 요청과 다릅니다"
            )
        verified_count = self.check_connection(configured_names)
        if catalog_count != verified_count:
            raise SupabaseWriteError(
                "Supabase 계정 카탈로그 동기화 후 계정 수가 일치하지 않습니다"
            )
        return CatalogSyncResult(requested_count, inserted_count, catalog_count)

    def write_daily_snapshot(
        self,
        reports: Mapping[str, Mapping[str, Any]],
        *,
        report_date: date | str | None = None,
        reported_at: datetime | None = None,
    ) -> SnapshotWriteResult:
        """Atomically upsert one complete daily snapshot.

        Every account in ``pb_algorithm_accounts`` must be present. This prevents a
        failed or incomplete collection from replacing a valid daily snapshot.
        """
        try:
            validate_complete_reports(reports)
        except PortfolioContractError as error:
            raise SupabaseWriteError(
                f"완전한 계정 snapshot 계약 불일치: {error}"
            ) from error

        snapshot_time = self._normalize_reported_at(reported_at)
        snapshot_date = self._normalize_report_date(report_date, snapshot_time)
        report_date_text = snapshot_date.isoformat()
        reported_at_text = snapshot_time.isoformat()
        source_message_ts = round(snapshot_time.timestamp(), 6)

        catalog = self._validate_catalog(self._load_account_catalog())
        catalog_by_name = {
            normalize_display_name(row["jenkins_name"]): row["account_id"] for row in catalog
        }
        expected_account_ids = set(catalog_by_name.values())

        balance_rows: list[dict[str, Any]] = []
        mapped_account_ids: set[str] = set()
        for report_name, report in reports.items():
            account_id = catalog_by_name.get(normalize_display_name(report_name))
            if not account_id:
                raise SupabaseWriteError(
                    f"DB 계정 카탈로그에서 Jenkins 이름을 찾지 못했습니다: {report_name}"
                )
            if account_id in mapped_account_ids:
                raise SupabaseWriteError(f"동일한 DB 계정이 중복 매핑되었습니다: {account_id}")

            try:
                money = canonical_money_breakdown(report_name, report)
            except PortfolioContractError as error:
                raise SupabaseWriteError(str(error)) from error

            mapped_account_ids.add(account_id)
            balance_rows.append(
                {
                    "account_id": account_id,
                    "total_value": float(money.total),
                    "position_value": float(money.position),
                    "cash_value": float(money.cash),
                }
            )

        missing_account_ids = expected_account_ids - mapped_account_ids
        if missing_account_ids:
            raise SupabaseWriteError(
                "일부 DB 계정의 리포트가 없어 적재를 중단합니다: "
                + ", ".join(sorted(missing_account_ids))
            )

        # The dashboard reconciles the portfolio row against the persisted account
        # rows. Sum those already-rounded values so independent grand-total
        # rounding can never manufacture a one-cent mismatch.
        total_decimal = sum(
            (Decimal(str(row["total_value"])) for row in balance_rows), Decimal("0")
        )
        position_decimal = sum(
            (Decimal(str(row["position_value"])) for row in balance_rows),
            Decimal("0"),
        )
        cash_decimal = sum(
            (Decimal(str(row["cash_value"])) for row in balance_rows), Decimal("0")
        )
        if total_decimal != position_decimal + cash_decimal:
            raise SupabaseWriteError("canonical portfolio total이 position + cash와 다릅니다")

        try:
            response = self.client.rpc(
                self.SNAPSHOT_RPC,
                {
                    "p_report_date": report_date_text,
                    "p_reported_at": reported_at_text,
                    "p_source_message_ts": source_message_ts,
                    "p_source_schema_version": PORTFOLIO_REPORT_SCHEMA_VERSION,
                    "p_balances": balance_rows,
                },
            ).execute()
        except Exception as exc:
            raise SupabaseWriteError(
                "Supabase atomic snapshot RPC 실패(unsafe fallback 없음): "
                + safe_error_message(exc)
            ) from exc

        result = self._rpc_object(response.data, "atomic snapshot RPC")
        try:
            result_total = self._decimal_value(
                result.get("total_value"), "portfolio", "total_value"
            )
            result_position = self._decimal_value(
                result.get("position_value"), "portfolio", "position_value"
            )
            result_cash = self._decimal_value(
                result.get("cash_value"), "portfolio", "cash_value"
            )
            result_count = int(result.get("account_count"))
        except (TypeError, ValueError, SupabaseWriteError) as error:
            raise SupabaseWriteError("Supabase atomic snapshot RPC 응답이 불완전합니다") from error
        if (
            result.get("report_date") != report_date_text
            or result_count != len(balance_rows)
            or result_total != total_decimal
            or result_position != position_decimal
            or result_cash != cash_decimal
        ):
            raise SupabaseWriteError("Supabase atomic snapshot RPC 대사 결과가 요청과 다릅니다")

        return SnapshotWriteResult(
            report_date=report_date_text,
            account_count=len(balance_rows),
            total_value=float(total_decimal),
            position_value=float(position_decimal),
            cash_value=float(cash_decimal),
        )

    def get_period_pnl(
        self,
        reports: Mapping[str, Mapping[str, Any]],
        *,
        windows: Sequence[int] = (7, 30),
        as_of: date | None = None,
    ) -> dict[str, dict[int, float | None]]:
        """Period P&L per account = current total_value − baseline total_value.

        ``baseline(window)`` is a snapshot on the window floor or at most one day
        later. A newer row does not prove coverage for the named period and yields
        ``None``. Deposits/withdrawals are not adjusted, same as the dashboard.

        Returns ``{report_name: {window: pnl_or_None}}``; ``None`` means there is
        no snapshot old enough to anchor that window.
        """
        if not reports or not windows:
            return {}
        resolved_as_of = as_of or datetime.now(self.timezone).date()

        catalog_by_name = {
            normalize_display_name(row["jenkins_name"]): row["account_id"]
            for row in self._validate_catalog(self._load_account_catalog())
        }

        name_to_account: dict[str, str] = {}
        current_totals: dict[str, float] = {}
        for report_name, report in reports.items():
            if "error" in report:
                continue
            account_id = catalog_by_name.get(normalize_display_name(report_name))
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
            raise SupabaseWriteError(
                "기간 손익용 과거 스냅샷 조회 실패: " + safe_error_message(exc)
            ) from exc

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
        only "current" value. For each window the baseline must be on the window
        floor or the following calendar day.
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
                latest_acceptable = (as_of - timedelta(days=window - 2)).isoformat()
                baseline = next(
                    (
                        total
                        for report_date, total in rows
                        if floor <= report_date <= latest_acceptable
                    ),
                    None,
                )
                per_window[window] = (
                    None if baseline is None else round(current_total - baseline, 6)
                )
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
            raise SupabaseWriteError(
                "Supabase 계정 카탈로그 조회 실패: " + safe_error_message(exc)
            ) from exc

        rows = response.data or []
        if not rows:
            raise SupabaseWriteError("Supabase 계정 카탈로그가 비어 있습니다")
        return rows

    @staticmethod
    def _validate_catalog(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
        """Require unique, non-empty display names and stable account IDs."""
        actual: dict[str, str] = {}
        account_ids: set[str] = set()
        for row in rows:
            try:
                display_name = normalize_display_name(str(row["jenkins_name"]))
                account_id = str(row["account_id"]).strip()
            except (KeyError, TypeError) as error:
                raise SupabaseWriteError("Supabase 계정 카탈로그 row가 불완전합니다") from error
            if not display_name or not account_id:
                raise SupabaseWriteError("Supabase 계정 카탈로그에 빈 이름/ID가 있습니다")
            if display_name in actual:
                raise SupabaseWriteError(
                    f"Supabase 계정 카탈로그 Jenkins 이름이 중복됩니다: {display_name}"
                )
            if account_id in account_ids:
                raise SupabaseWriteError(
                    f"Supabase 계정 카탈로그 stable ID가 중복됩니다: {account_id}"
                )
            actual[display_name] = account_id
            account_ids.add(account_id)
        return [
            {"jenkins_name": name, "account_id": account_id}
            for name, account_id in actual.items()
        ]

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
        if not number.is_finite():
            raise SupabaseWriteError(f"{account_name}의 {field_name} 값이 유한한 숫자가 아닙니다")
        return number

    @staticmethod
    def _rpc_object(value: Any, context: str) -> Mapping[str, Any]:
        if isinstance(value, Mapping):
            return value
        if (
            isinstance(value, list)
            and len(value) == 1
            and isinstance(value[0], Mapping)
        ):
            return value[0]
        raise SupabaseWriteError(f"{context} 응답이 object가 아닙니다")
