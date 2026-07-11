"""Persist secret-safe, immutable run and configuration telemetry."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_CURRENT_RUN_ID: ContextVar[str | None] = ContextVar("polybot_run_id", default=None)
_MAX_ERROR_LENGTH = 2_000
_MAX_SANITIZER_INPUT = 32_000
_SENSITIVE_FIELD_NAME = re.compile(
    r"(?i)(private|secret|password|passwd|credential|authorization|"
    r"api[_ -]?key|access[_ -]?token|refresh[_ -]?token|session[_ -]?token|"
    r"id[_ -]?token|auth[_ -]?token|token|webhook|dsn|database[_ -]?url|"
    r"db[_ -]?url|connection[_ -]?string|service[_ -]?role[_ -]?key|"
    r"signing[_ -]?key)"
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?ix)"
    r"(?<![a-z0-9_-])"
    r"(?P<prefix>['\"]?(?P<key>[a-z][a-z0-9_ -]{0,63})['\"]?\s*[:=]\s*)"
    r"(?:"
    r"(?P<quote>['\"])(?P<quoted>[^'\"]*)(?P=quote)?"
    r"|(?P<bare>[^\s,;{}\[\]'\"]+)"
    r")"
)
_AUTHORIZATION_HEADER = re.compile(
    r"(?ix)"
    r"(?P<prefix>['\"]?authorization['\"]?\s*[:=]\s*)"
    r"(?:"
    r"(?P<quote>['\"])(?P<quoted>[^'\"]*)(?P=quote)?"
    r"|(?P<bare>(?:(?:bearer|basic)\s+)?[^\s,;}\]]+)"
    r")"
)
_CREDENTIALED_DATABASE_DSN = re.compile(
    r"(?ix)\b(?:jdbc:)?(?:postgres(?:ql)?|mysql|mariadb|"
    r"mongodb(?:\+srv)?|redis(?:s)?|amqp(?:s)?|mssql|oracle)"
    r"(?:\+[a-z0-9_]+)?://[^\s:/@'\"]+:[^\s/@'\"]+@"
    r"[^\s,;'\"}\])]+"
)
_KNOWN_BARE_SECRET = re.compile(
    r"(?x)(?<![A-Za-z0-9])(?:"
    r"gh[pousr]_[A-Za-z0-9]{20,255}|"
    r"github_pat_[A-Za-z0-9_]{20,255}|"
    r"(?:AKIA|ASIA|AIDA|AROA|AIPA|ANPA|ANVA|ABIA|ACCA|AGPA)[A-Z0-9]{16}|"
    r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,255}|"
    r"sk_(?:live|test)_[A-Za-z0-9]{16,255}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,255}|"
    r"sb_secret_[A-Za-z0-9_-]{10,255}|"
    r"AIza[A-Za-z0-9_-]{30,255}|"
    r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]{10,255}"
    r")(?![A-Za-z0-9])"
)
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{5,}\."
    r"[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*(?![A-Za-z0-9_-])"
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"(?is)-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?"
    r"(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)"
)
_CHAIN_IDENTIFIER = re.compile(r"(?i)\b(?:0x)?[a-f0-9]{40}(?:[a-f0-9]{24})?\b")
_COMMIT_SHA = re.compile(r"^[a-fA-F0-9]{7,64}$")
_SENSITIVE_KEY = _SENSITIVE_FIELD_NAME


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def current_run_id() -> str | None:
    """Return the run currently executing in this process, if any."""
    return _CURRENT_RUN_ID.get()


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, (set, tuple)):
        return list(value)
    raise TypeError(f"JSON으로 직렬화할 수 없는 값입니다: {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=_json_default,
    )


def _redact_config_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "<redacted>" if _SENSITIVE_KEY.search(str(key)) else _redact_config_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_config_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_config_value(item) for item in value)
    return value


def _safe_error_message(error: BaseException) -> str:
    message = " ".join(str(error).splitlines())[:_MAX_SANITIZER_INPUT]
    # Exception reprs often embed JSON/Python dicts with escaped quotes.
    message = message.replace(r'\"', '"').replace(r"\'", "'")
    message = _PRIVATE_KEY_BLOCK.sub("<redacted-private-key>", message)
    message = _AUTHORIZATION_HEADER.sub(_redact_assignment, message)
    message = _SENSITIVE_ASSIGNMENT.sub(_redact_assignment, message)
    message = _CREDENTIALED_DATABASE_DSN.sub("<redacted-dsn>", message)
    message = _KNOWN_BARE_SECRET.sub("<redacted-secret>", message)
    message = _JWT.sub("<redacted-jwt>", message)
    message = _CHAIN_IDENTIFIER.sub("<redacted-chain-id>", message)
    return message[:_MAX_ERROR_LENGTH]


def _redact_assignment(match: re.Match[str]) -> str:
    key = match.groupdict().get("key")
    if key is not None and not _SENSITIVE_FIELD_NAME.search(key):
        return match.group(0)
    quote = match.groupdict().get("quote") or ""
    return f"{match.group('prefix')}{quote}<redacted>{quote}"


def _resolved_config(config: Any, strategy_name: str) -> dict[str, Any]:
    """Return the explicitly allow-listed, secret-free resolved config."""
    trading = config.trading
    if dataclasses.is_dataclass(trading):
        trading_payload = dataclasses.asdict(trading)
    elif isinstance(trading, Mapping):
        trading_payload = dict(trading)
    else:
        raise TypeError("config.trading은 dataclass 또는 mapping이어야 합니다")

    # Deliberately omit config.api, db_path and every environment variable.
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_name": strategy_name,
        "mode": "sim" if bool(config.simulation_mode) else "live",
        "trading": _redact_config_value(trading_payload),
    }


def _git_commit() -> str:
    value = (os.getenv("GIT_COMMIT") or "").strip()
    if value:
        return value if _COMMIT_SHA.fullmatch(value) else "unknown"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    value = result.stdout.strip()
    return value if _COMMIT_SHA.fullmatch(value) else "unknown"


class RunAudit:
    """Lifecycle recorder backed by the bot's existing SQLite database."""

    def __init__(
        self,
        *,
        db_path: Path,
        run_id: str,
        strategy_name: str,
        job_name: str,
        mode: str,
        config_hash: str,
        git_commit: str,
    ) -> None:
        self.db_path = db_path
        self.run_id = run_id
        self.strategy_name = strategy_name
        self.job_name = job_name
        self.mode = mode
        self.config_hash = config_hash
        self.git_commit = git_commit
        self._finished = False
        self._context_token: Token[str | None] | None = None

    @classmethod
    def start(cls, config: Any, *, strategy_name: str) -> RunAudit:
        """Create a RUNNING row before any trading action starts."""
        payload = _resolved_config(config, strategy_name)
        config_json = _canonical_json(payload)
        config_hash = hashlib.sha256(config_json.encode("utf-8")).hexdigest()
        run_id = str(uuid.uuid4())
        started_at = _utc_now()
        git_commit = _git_commit()
        mode = payload["mode"]
        db_path = Path(config.db_path).expanduser().resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)

        recorder = cls(
            db_path=db_path,
            run_id=run_id,
            strategy_name=strategy_name,
            job_name=str(config.job_name),
            mode=mode,
            config_hash=config_hash,
            git_commit=git_commit,
        )
        with recorder._connect() as connection:
            recorder._ensure_schema(connection)
            connection.execute(
                """
                INSERT OR IGNORE INTO strategy_configs
                    (config_hash, schema_version, strategy_name, mode, config_json,
                     first_seen_at, git_commit)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    config_hash,
                    SCHEMA_VERSION,
                    strategy_name,
                    mode,
                    config_json,
                    started_at,
                    git_commit,
                ),
            )
            connection.execute(
                """
                INSERT INTO run_audits
                    (run_id, schema_version, strategy_name, job_name, mode,
                     config_hash, git_commit, started_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'RUNNING')
                """,
                (
                    run_id,
                    SCHEMA_VERSION,
                    strategy_name,
                    str(config.job_name),
                    mode,
                    config_hash,
                    git_commit,
                    started_at,
                ),
            )
        recorder._context_token = _CURRENT_RUN_ID.set(run_id)
        logger.info(
            "[RUN_AUDIT] 시작 run_id=%s strategy=%s mode=%s config=%s commit=%s",
            run_id,
            strategy_name,
            mode,
            config_hash[:12],
            git_commit[:12],
        )
        return recorder

    def succeed(self, stats: Mapping[str, Any] | None = None) -> None:
        """Finalize the run with cycle statistics and a generic DB summary."""
        if self._finished:
            return
        with self._connect() as connection:
            db_summary = self._database_summary(connection)
            connection.execute(
                """
                UPDATE run_audits
                SET finished_at = ?, status = 'SUCCESS', cycle_stats_json = ?,
                    db_summary_json = ?
                WHERE run_id = ?
                """,
                (
                    _utc_now(),
                    _canonical_json(dict(stats or {})),
                    _canonical_json(db_summary),
                    self.run_id,
                ),
            )
        self._finished = True
        self._restore_context()
        logger.info("[RUN_AUDIT] 성공 run_id=%s", self.run_id)

    def fail(self, error: BaseException) -> None:
        """Finalize the run without serializing tracebacks or credentials."""
        if self._finished:
            return
        message = _safe_error_message(error)
        with self._connect() as connection:
            db_summary = self._database_summary(connection)
            connection.execute(
                """
                UPDATE run_audits
                SET finished_at = ?, status = 'FAILED', db_summary_json = ?,
                    error_type = ?, error_message = ?
                WHERE run_id = ?
                """,
                (
                    _utc_now(),
                    _canonical_json(db_summary),
                    type(error).__name__,
                    message,
                    self.run_id,
                ),
            )
        self._finished = True
        self._restore_context()
        logger.warning(
            "[RUN_AUDIT] 실패 run_id=%s error=%s", self.run_id, type(error).__name__
        )

    def _restore_context(self) -> None:
        if self._context_token is not None:
            _CURRENT_RUN_ID.reset(self._context_token)
            self._context_token = None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            BEGIN IMMEDIATE;

            CREATE TABLE IF NOT EXISTS polybot_schema_versions (
                component TEXT PRIMARY KEY,
                version INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS strategy_configs (
                config_hash TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                strategy_name TEXT NOT NULL,
                mode TEXT NOT NULL,
                config_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                git_commit TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS run_audits (
                run_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                strategy_name TEXT NOT NULL,
                job_name TEXT NOT NULL,
                mode TEXT NOT NULL,
                config_hash TEXT NOT NULL REFERENCES strategy_configs(config_hash),
                git_commit TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                cycle_stats_json TEXT,
                db_summary_json TEXT,
                error_type TEXT,
                error_message TEXT
            );

            CREATE INDEX IF NOT EXISTS run_audits_strategy_started_idx
                ON run_audits(strategy_name, started_at DESC);
            CREATE INDEX IF NOT EXISTS run_audits_config_started_idx
                ON run_audits(config_hash, started_at DESC);
            """
        )
        required_columns = {
            "strategy_configs": {
                "config_hash", "schema_version", "strategy_name", "mode",
                "config_json", "first_seen_at", "git_commit",
            },
            "run_audits": {
                "run_id", "schema_version", "strategy_name", "job_name",
                "mode", "config_hash", "git_commit", "started_at",
                "finished_at", "status", "cycle_stats_json",
                "db_summary_json", "error_type", "error_message",
            },
        }
        for table, required in required_columns.items():
            columns = {
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            missing = required - columns
            if missing:
                raise RuntimeError(
                    f"{table} schema에 필수 컬럼이 없습니다: {sorted(missing)}"
                )
        connection.execute(
            """
            INSERT INTO polybot_schema_versions(component, version, updated_at)
            VALUES ('run_audit', ?, ?)
            ON CONFLICT(component) DO UPDATE SET
                version = excluded.version, updated_at = excluded.updated_at
            """,
            (SCHEMA_VERSION, _utc_now()),
        )

    @staticmethod
    def _database_summary(connection: sqlite3.Connection) -> dict[str, Any]:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        summary: dict[str, Any] = {}
        if "trades" in tables:
            summary["trades_total"] = connection.execute(
                "SELECT COUNT(*) FROM trades"
            ).fetchone()[0]
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(trades)")
            }
            if "status" in columns:
                summary["trade_status_counts"] = {
                    str(status): count
                    for status, count in connection.execute(
                        "SELECT status, COUNT(*) FROM trades GROUP BY status"
                    )
                }
        if "market_snapshots" in tables:
            count, first_seen, last_seen = connection.execute(
                "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM market_snapshots"
            ).fetchone()
            summary["market_snapshots"] = {
                "count": count,
                "first_seen": first_seen,
                "last_seen": last_seen,
            }
        if "skipped_markets" in tables:
            summary["skipped_total"] = connection.execute(
                "SELECT COUNT(*) FROM skipped_markets"
            ).fetchone()[0]
        if "order_submissions" in tables:
            total, pending = connection.execute(
                "SELECT COUNT(*), SUM(CASE WHEN needs_reconciliation = 1 THEN 1 ELSE 0 END) "
                "FROM order_submissions"
            ).fetchone()
            summary["order_submissions"] = {
                "count": total,
                "pending_reconciliation": pending or 0,
            }
        if "order_fills" in tables:
            total, confirmed = connection.execute(
                "SELECT COUNT(*), SUM(CASE WHEN status = 'CONFIRMED' THEN 1 ELSE 0 END) "
                "FROM order_fills"
            ).fetchone()
            summary["order_fills"] = {"count": total, "confirmed": confirmed or 0}
        return summary
