"""Exponential backoff wrapper for LLM calls."""
import asyncio
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger()

_RETRYABLE = (Exception,)  # tighten per provider if needed


def llm_retry():
    """Return a tenacity AsyncRetrying configured for LLM calls."""
    return AsyncRetrying(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        before_sleep=lambda retry_state: log.warning(
            "llm.retry",
            attempt=retry_state.attempt_number,
            exc=str(retry_state.outcome.exception()),
        ),
        reraise=True,
    )
