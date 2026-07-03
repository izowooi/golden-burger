"""Logging configuration."""
import logging
import os
import sys
from pathlib import Path
from datetime import datetime

_LEVEL_NAMES = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def resolve_log_level(cli_level: int = None) -> int:
    """로그 레벨 결정 (§3.1: LOG_LEVEL env 지원).

    우선순위: CLI --verbose(DEBUG) > LOG_LEVEL env > INFO.
    기존 봇은 Jenkins가 export하는 LOG_LEVEL을 무시했다 — 수정.

    Args:
        cli_level: CLI에서 명시한 레벨 (--verbose면 logging.DEBUG)

    Returns:
        logging 레벨 상수
    """
    if cli_level is not None:
        return cli_level
    env_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    return _LEVEL_NAMES.get(env_level, logging.INFO)


def setup_logger(job_name: str = "default", level: int = None):
    """Set up logging with file and console handlers.

    Args:
        job_name: Job name for log file organization
        level: Logging level. None이면 LOG_LEVEL env(기본 INFO)를 따른다
    """
    level = resolve_log_level(level)

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
