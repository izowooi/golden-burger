from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from slack_data_collector.client import SlackApiError, SlackWebClient
from slack_data_collector.collector import SlackChannelCollector
from slack_data_collector.config import ConfigurationError, Settings
from slack_data_collector.portfolio import (
    PortfolioParseError,
    transform_portfolio_reports,
)
from slack_data_collector.portfolio_sql import export_upsert_sql
from slack_data_collector.storage import store_collection
from slack_data_collector.time_range import TimeRange, TimeRangeError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slack-data-collector",
        description="특정 기간의 Slack 채널 메시지를 수집하고 JSONL로 정규화합니다.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Slack 인증 환경변수 파일 (기본값: .env)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check", help="토큰과 채널 접근 권한을 확인합니다.")

    collect_parser = subparsers.add_parser(
        "collect", help="지정 기간의 메시지를 수집합니다."
    )
    collect_parser.add_argument(
        "--start", required=True, help="수집 시작일 (YYYY-MM-DD, 포함)"
    )
    collect_parser.add_argument(
        "--end", required=True, help="수집 종료일 (YYYY-MM-DD, 포함)"
    )
    collect_parser.add_argument(
        "--timezone",
        default="Asia/Seoul",
        help="날짜 경계 타임존 (기본값: Asia/Seoul)",
    )
    collect_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data"),
        help="결과 저장 루트 (기본값: data)",
    )
    collect_parser.add_argument(
        "--no-threads",
        action="store_true",
        help="스레드 답글을 수집하지 않습니다.",
    )
    collect_parser.add_argument(
        "--bounded-thread-scan",
        action="store_true",
        help="기간 안의 부모 글만 검사합니다. 빠르지만 오래된 글의 기간 내 답글이 누락될 수 있습니다.",
    )

    portfolio_parser = subparsers.add_parser(
        "portfolio", help="Slack 원본 JSONL에서 날짜별 포트폴리오 잔고를 추출합니다."
    )
    portfolio_parser.add_argument(
        "--input", type=Path, required=True, help="raw/messages.jsonl 경로"
    )
    portfolio_parser.add_argument(
        "--output-dir",
        type=Path,
        help="출력 디렉터리 (기본값: 실행 디렉터리/portfolio)",
    )
    portfolio_parser.add_argument(
        "--export-sql",
        action="store_true",
        help="Supabase upsert SQL을 배치 파일로 생성합니다.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "portfolio":
            return _portfolio(args)
        settings = Settings.from_environment(args.env_file)
        client = SlackWebClient(settings.bot_token)
        if args.command == "check":
            return _check(client, settings)
        return _collect(client, settings, args)
    except (
        ConfigurationError,
        TimeRangeError,
        SlackApiError,
        PortfolioParseError,
        OSError,
        ValueError,
    ) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1


def _check(client: SlackWebClient, settings: Settings) -> int:
    auth = client.auth_test()
    info = client.conversation_info(settings.channel_id)
    channel = _object(info.get("channel"))
    safe_result = {
        "ok": True,
        "workspace_id": auth.get("team_id"),
        "workspace_name": auth.get("team"),
        "bot_user_id": auth.get("user_id"),
        "channel_id": channel.get("id", settings.channel_id),
        "channel_name": channel.get("name"),
        "is_private": channel.get("is_private"),
        "is_member": channel.get("is_member"),
    }
    print(json.dumps(safe_result, ensure_ascii=False, indent=2))
    return 0


def _collect(
    client: SlackWebClient, settings: Settings, args: argparse.Namespace
) -> int:
    time_range = TimeRange.from_dates(args.start, args.end, args.timezone)
    auth = client.auth_test()
    info = client.conversation_info(settings.channel_id)
    channel = _object(info.get("channel"))
    complete_thread_scan = not args.bounded_thread_scan

    collector = SlackChannelCollector(client)
    result = collector.collect(
        settings.channel_id,
        time_range,
        include_threads=not args.no_threads,
        complete_thread_scan=complete_thread_scan,
    )
    stored = store_collection(
        result,
        output_root=args.output_dir,
        workspace_id=str(auth.get("team_id") or ""),
        channel_id=settings.channel_id,
        channel_name=str(channel.get("name")) if channel.get("name") else None,
        time_range=time_range,
        complete_thread_scan=complete_thread_scan,
    )
    output = {
        "ok": True,
        "period": {
            "start": time_range.start_date,
            "end": time_range.end_date,
            "timezone": time_range.timezone_name,
        },
        "stats": result.stats.to_dict(),
        "run_directory": str(stored.run_directory),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _portfolio(args: argparse.Namespace) -> int:
    output_directory = args.output_dir or args.input.parent.parent / "portfolio"
    result = transform_portfolio_reports(args.input, output_directory)
    output = result.summary()
    if args.export_sql:
        sql_paths = export_upsert_sql(output_directory, output_directory / "sql")
        output["sql_files"] = [str(path) for path in sql_paths]
    print(json.dumps({"ok": True, **output}, ensure_ascii=False, indent=2))
    return 0


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
