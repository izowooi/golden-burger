"""Logging configuration."""
import logging
import os
import sys
from pathlib import Path
from datetime import datetime


def _level_from_env() -> int:
    """LOG_LEVEL 환경변수에서 로그 레벨 결정 (기본 INFO).

    Jenkins 스크립트가 export하는 LOG_LEVEL을 기존 봇들이 무시하던 문제 수정.
    """
    name = os.getenv("LOG_LEVEL", "INFO").upper()
    return getattr(logging, name, logging.INFO)


def setup_logger(job_name: str = "default", level: int = None):
    """Set up logging with file and console handlers.

    레벨 우선순위: level 인자(--verbose의 DEBUG) > LOG_LEVEL env > INFO.

    Args:
        job_name: Job name for log file organization
        level: Logging level override (None이면 LOG_LEVEL env 사용)
    """
    if level is None:
        level = _level_from_env()

    # Create log directory
    log_dir = Path("data") / job_name / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Log file path
    log_file = log_dir / f"{datetime.now().strftime('%Y%m%d')}.log"

    # Formatter
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # File handler
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers
    root_logger.handlers.clear()

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Reduce noise from third-party libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    logging.info(f"로깅 초기화 완료 - Job: {job_name}, Log file: {log_file}")
