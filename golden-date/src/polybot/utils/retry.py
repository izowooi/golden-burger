"""Retry and rate limit handling utilities."""
import logging
import math
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import wraps

import requests

logger = logging.getLogger(__name__)
MAX_RETRY_DELAY_SECONDS = 60.0


def _bounded_delay(seconds: float) -> float:
    """Return a finite, non-negative retry delay with a hard wall-clock cap."""
    if not math.isfinite(seconds):
        return 0.0
    return min(MAX_RETRY_DELAY_SECONDS, max(0.0, seconds))


def _retry_after_seconds(value, fallback: float) -> float:
    """Parse Retry-After delta-seconds or HTTP-date without trusting it unboundedly."""
    if value is None:
        return _bounded_delay(fallback)
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(value))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            seconds = fallback
    if not math.isfinite(seconds):
        seconds = fallback
    return _bounded_delay(seconds)


def rate_limit_handler(
    max_retries: int = 5,
    base_delay: float = 2.0,
    *,
    retry_forbidden: bool = False,
):
    """Decorator for handling rate limits and transient errors.

    Implements exponential backoff with jitter for:
    - HTTP 429 (Rate Limit)
    - HTTP 5xx (Server Errors)
    - Connection errors

    ``retry_forbidden`` is intentionally opt-in for public endpoints where a
    mid-stream 403 is known to be a transient edge/WAF throttle. Authenticated
    API 403 responses must continue to fail immediately.

    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Base delay in seconds for backoff

    Returns:
        Decorated function
    """
    if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 1:
        raise ValueError("max_retries must be a positive integer")
    if not math.isfinite(base_delay) or base_delay < 0:
        raise ValueError("base_delay must be finite and non-negative")
    if not isinstance(retry_forbidden, bool):
        raise ValueError("retry_forbidden must be a boolean")

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)

                except requests.exceptions.HTTPError as e:
                    last_exception = e
                    status_code = e.response.status_code if e.response is not None else 0

                    if status_code == 429 or (
                        status_code == 403 and retry_forbidden
                    ):
                        exponential_delay = _bounded_delay(
                            base_delay * (2 ** attempt)
                        )
                        retry_after = _retry_after_seconds(
                            e.response.headers.get("Retry-After"), base_delay
                        )
                        wait_time = _bounded_delay(
                            max(retry_after, exponential_delay)
                            + random.uniform(0, 1)
                        )

                        if attempt + 1 < max_retries:
                            logger.warning(
                                f"Transient HTTP {status_code} "
                                f"(시도 {attempt + 1}/{max_retries}), "
                                f"{wait_time:.1f}초 대기..."
                            )
                            time.sleep(wait_time)

                    elif status_code in (500, 502, 503, 504):
                        # Server error - exponential backoff
                        wait_time = _bounded_delay(
                            base_delay * (2 ** attempt) + random.uniform(0, 1)
                        )

                        if attempt + 1 < max_retries:
                            logger.warning(
                                f"Server error {status_code} "
                                f"(시도 {attempt + 1}/{max_retries}), "
                                f"{wait_time:.1f}초 대기..."
                            )
                            time.sleep(wait_time)

                    else:
                        # Other HTTP errors - don't retry
                        raise

                except requests.exceptions.ConnectionError as e:
                    last_exception = e
                    wait_time = _bounded_delay(
                        base_delay * (2 ** attempt) + random.uniform(0, 1)
                    )

                    if attempt + 1 < max_retries:
                        logger.warning(
                            f"Connection error (시도 {attempt + 1}/{max_retries}), "
                            f"{wait_time:.1f}초 대기..."
                        )
                        time.sleep(wait_time)

                except requests.exceptions.Timeout as e:
                    last_exception = e
                    wait_time = _bounded_delay(base_delay * (2 ** attempt))

                    if attempt + 1 < max_retries:
                        logger.warning(
                            f"Timeout (시도 {attempt + 1}/{max_retries}), "
                            f"{wait_time:.1f}초 대기..."
                        )
                        time.sleep(wait_time)

            # All retries exhausted
            logger.error(f"최대 재시도 횟수 ({max_retries}) 초과")
            raise last_exception or Exception(f"Failed after {max_retries} retries")

        return wrapper
    return decorator
