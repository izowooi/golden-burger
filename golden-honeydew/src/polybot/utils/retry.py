"""Retry and rate limit handling utilities."""
import time
import random
import logging
from functools import wraps
import requests

logger = logging.getLogger(__name__)


def rate_limit_handler(max_retries: int = 5, base_delay: float = 2.0):
    """Decorator for handling rate limits and transient errors.

    Implements exponential backoff with jitter for:
    - HTTP 429 (Rate Limit)
    - HTTP 5xx (Server Errors)
    - Connection errors

    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Base delay in seconds for backoff

    Returns:
        Decorated function
    """
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

                    if status_code == 429:
                        # Rate limit - use Retry-After header if available
                        retry_after = int(e.response.headers.get("Retry-After", base_delay))
                        jitter = random.uniform(0, 1)
                        wait_time = retry_after + jitter

                        logger.warning(
                            f"Rate limit 도달 (시도 {attempt + 1}/{max_retries}), "
                            f"{wait_time:.1f}초 대기..."
                        )
                        time.sleep(wait_time)

                    elif status_code in (500, 502, 503, 504):
                        # Server error - exponential backoff
                        wait_time = base_delay * (2 ** attempt) + random.uniform(0, 1)

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
                    wait_time = base_delay * (2 ** attempt) + random.uniform(0, 1)

                    logger.warning(
                        f"Connection error (시도 {attempt + 1}/{max_retries}), "
                        f"{wait_time:.1f}초 대기..."
                    )
                    time.sleep(wait_time)

                except requests.exceptions.Timeout as e:
                    last_exception = e
                    wait_time = base_delay * (2 ** attempt)

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
