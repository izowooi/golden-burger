"""Logging configuration."""
import logging
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional


def resolve_log_level(override: Optional[int] = None) -> int:
    """로그 레벨 결정: --verbose(override) > LOG_LEVEL env > INFO.

    Args:
        override: CLI --verbose 등 최우선 오버라이드 레벨

    Returns:
        logging level (int)
    """
    if override is not None:
        return override
    env_level = os.getenv("LOG_LEVEL", "INFO").upper()
    return getattr(logging, env_level, logging.INFO)


def setup_logger(job_name: str = "default", level: Optional[int] = None):
    """Set up logging with file and console handlers.

    LOG_LEVEL 환경변수(기본 INFO)를 읽으며, level 인자가 주어지면
    (--verbose의 DEBUG 등) 그것이 최우선이다.

    Args:
        job_name: Job name for log file organization
        level: Logging level override (None이면 LOG_LEVEL env 사용)
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

    logging.info(
        f"로깅 초기화 완료 - Job: {job_name}, "
        f"Level: {logging.getLevelName(level)}, Log file: {log_file}"
    )
