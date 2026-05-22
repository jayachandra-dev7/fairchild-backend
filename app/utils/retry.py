from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx

logger = logging.getLogger(__name__)
T = TypeVar('T')

TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


def is_retryable_httpx_error(exc: Exception) -> tuple[bool, int | None]:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code in TRANSIENT_STATUS_CODES, status_code
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError, httpx.NetworkError)):
        return True, None
    return False, None


async def run_with_retry(
    *,
    operation: Callable[[], Awaitable[T]],
    step: str,
    max_attempts: int = 3,
) -> T:
    delay_schedule = [0.5, 1.0, 2.0]
    attempt = 0
    while True:
        attempt += 1
        try:
            return await operation()
        except Exception as exc:  # noqa: BLE001
            retryable, status_code = is_retryable_httpx_error(exc)
            should_retry = retryable and attempt < max_attempts
            logger.warning(
                'pipeline_attempt_failed',
                extra={
                    'step': step,
                    'attempt_no': attempt,
                    'status_code': status_code,
                    'retryable': retryable,
                    'error_code': exc.__class__.__name__,
                },
            )
            if not should_retry:
                raise
            base = delay_schedule[min(attempt - 1, len(delay_schedule) - 1)]
            jitter = random.uniform(0.0, 0.15)
            await asyncio.sleep(base + jitter)
