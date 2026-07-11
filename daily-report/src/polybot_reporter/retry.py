import logging
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import wraps

import requests

logger = logging.getLogger(__name__)
DEFAULT_MAX_RETRY_DELAY_SECONDS = 30.0


def _retry_after_seconds(
    value: str | None,
    *,
    fallback: float,
    maximum: float,
    now: datetime | None = None,
) -> float:
    """Parse Retry-After without allowing malformed or huge sleeps."""
    bounded_fallback = min(max(float(fallback), 0.0), maximum)
    if not value:
        return bounded_fallback

    try:
        delay = float(int(value.strip()))
    except (TypeError, ValueError, OverflowError):
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            current = now or datetime.now(timezone.utc)
            delay = (retry_at - current).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return bounded_fallback

    return min(max(delay, 0.0), maximum)


def rate_limit_handler(
    max_retries: int = 5,
    base_delay: float = 2.0,
    max_delay: float = DEFAULT_MAX_RETRY_DELAY_SECONDS,
):
    """Decorator for handling rate limits and transient errors.

    Implements exponential backoff with jitter for:
    - HTTP 429 (Rate Limit)
    - HTTP 5xx (Server Errors)
    - Connection errors

    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Base delay in seconds for backoff
        max_delay: Hard upper bound for every sleep, including Retry-After

    Returns:
        Decorated function
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if max_retries < 1:
                raise ValueError("max_retries는 1 이상이어야 합니다")
            if max_delay < 0:
                raise ValueError("max_delay는 0 이상이어야 합니다")
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)

                except requests.exceptions.HTTPError as e:
                    last_exception = e
                    status_code = e.response.status_code if e.response is not None else 0

                    if status_code == 429:
                        if attempt == max_retries - 1:
                            break
                        # Retry-After supports delta-seconds and HTTP-date. Invalid
                        # values fall back to bounded exponential backoff.
                        retry_after = _retry_after_seconds(
                            e.response.headers.get("Retry-After"),
                            fallback=base_delay * (2**attempt),
                            maximum=max_delay,
                        )
                        jitter = random.uniform(0, 1)
                        wait_time = min(retry_after + jitter, max_delay)

                        logger.warning(
                            f"Rate limit 도달 (시도 {attempt + 1}/{max_retries}), "
                            f"{wait_time:.1f}초 대기..."
                        )
                        time.sleep(wait_time)

                    elif status_code in (500, 502, 503, 504):
                        if attempt == max_retries - 1:
                            break
                        # Server error - exponential backoff
                        wait_time = min(
                            max(base_delay * (2**attempt), 0.0) + random.uniform(0, 1),
                            max_delay,
                        )

                        logger.warning(
                            f"Server error {status_code} (시도 {attempt + 1}/{max_retries}), "
                            f"{wait_time:.1f}초 대기..."
                        )
                        time.sleep(wait_time)

                    else:
                        # Other HTTP errors - don't retry
                        raise

                except requests.exceptions.ConnectionError as e:
                    last_exception = e
                    if attempt == max_retries - 1:
                        break
                    wait_time = min(
                        max(base_delay * (2**attempt), 0.0) + random.uniform(0, 1),
                        max_delay,
                    )

                    logger.warning(
                        f"Connection error (시도 {attempt + 1}/{max_retries}), "
                        f"{wait_time:.1f}초 대기..."
                    )
                    time.sleep(wait_time)

                except requests.exceptions.Timeout as e:
                    last_exception = e
                    if attempt == max_retries - 1:
                        break
                    wait_time = min(max(base_delay * (2**attempt), 0.0), max_delay)

                    logger.warning(
                        f"Timeout (시도 {attempt + 1}/{max_retries}), {wait_time:.1f}초 대기..."
                    )
                    time.sleep(wait_time)

            # All retries exhausted
            logger.error(f"최대 재시도 횟수 ({max_retries}) 초과")
            raise last_exception or Exception(f"Failed after {max_retries} retries")

        return wrapper

    return decorator
