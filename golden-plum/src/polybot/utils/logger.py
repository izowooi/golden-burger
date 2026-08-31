"""Logging configuration."""
import logging
import os
import sys
from pathlib import Path
from datetime import datetime

DEFAULT_LEVEL = logging.INFO


def resolve_log_level(verbose: bool = False) -> int:
    """로그 레벨 결정 (§3.1).

    우선순위: --verbose(DEBUG) > LOG_LEVEL env > INFO.
    Jenkins 스크립트가 export하는 LOG_LEVEL을 실제로 반영한다.

    Args:
        verbose: CLI --verbose 플래그

    Returns:
        logging 레벨 상수
    """
    if verbose:
        return logging.DEBUG

    name = os.getenv("LOG_LEVEL", "").strip().upper()
    if name:
        level = logging.getLevelName(name)
        if isinstance(level, int):
            return level
        logging.getLogger(__name__).warning(f"알 수 없는 LOG_LEVEL: {name} - INFO 사용")
    return DEFAULT_LEVEL


def setup_logger(job_name: str = "default", level: int = None, verbose: bool = False):
    """Set up logging with file and console handlers.

    Args:
        job_name: Job name for log file organization
        level: 명시적 로그 레벨 (지정 시 env보다 우선, verbose보다는 후순위)
        verbose: True면 DEBUG로 최우선 오버라이드
    """
    if verbose:
        level = logging.DEBUG
    elif level is None:
        level = resolve_log_level()

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
