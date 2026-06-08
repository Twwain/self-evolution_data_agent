"""LLM 调用重试 + 失败留痕 (Phase 2 Gap #5).

独立模块, 从 extraction_prompts.py 提取.
设计: docs/superpowers/specs/2026-05-15-schema-knowledge-onboarding/07-failure-and-audit.md §7.1
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable

from app.config import settings

logger = logging.getLogger(__name__)

_RETRY_STATUS = {429, 500, 502, 503, 504}  # noqa: hardcode


def _classify_failure(exc: Exception) -> str:
    """Classify exception into failure_type for ExtractionFailureLog."""
    from app.engine.llm import EmptyLLMResponseError

    if isinstance(exc, EmptyLLMResponseError):
        return "llm_empty_response"
    if isinstance(exc, asyncio.TimeoutError):
        return "llm_timeout"
    # Check for HTTP status errors from openai/anthropic/httpx
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if status_code is not None:
        if status_code == 429:  # noqa: hardcode
            return "llm_rate_limited"
        if 500 <= status_code < 600:
            return "llm_server_error"
        if 400 <= status_code < 500:
            return "llm_4xx_error"
    return "llm_server_error"


def _is_retryable(exc: Exception) -> bool:
    """Should we retry this exception?"""
    from app.engine.llm import EmptyLLMResponseError

    if isinstance(exc, EmptyLLMResponseError):
        return True
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if status_code is not None:
        return status_code in _RETRY_STATUS
    # Connection errors are retryable
    exc_name = type(exc).__name__
    if "Connection" in exc_name or "Timeout" in exc_name:
        return True
    return False


async def with_retry(
    fn: Callable[[], Awaitable[Any]],
    *,
    template_name: str,
    input_meta: dict[str, Any],
    prompt_full: str,
    extraction_kind: str,
    db=None,
    namespace_id: int | None = None,
    repo_id: int | None = None,
) -> Any:
    """指数退避重试 + 失败完整日志契约.

    On final failure, automatically writes ExtractionFailureLog then re-raises.
    """
    from app.knowledge.explain_gate import write_extraction_failure

    attempts: list[dict[str, Any]] = []
    started = time.monotonic()
    max_attempts = settings.llm_retry_max_attempts
    base = settings.llm_retry_base_delay_secs

    for attempt in range(max_attempts):
        try:
            return await fn()
        except Exception as exc:
            failure_type = _classify_failure(exc)
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            should_retry = _is_retryable(exc)
            delay = base * (4 ** attempt) if should_retry and attempt < max_attempts - 1 else None

            attempts.append({
                "attempt": attempt,
                "error_type": exc.__class__.__name__,
                "status_code": status_code,
                "error_message": str(exc)[:500],
                "delay_after_secs": delay,
            })

            if not should_retry or attempt == max_attempts - 1:
                # Final failure — write log and raise
                await _write_failure(
                    db, namespace_id, repo_id, extraction_kind,
                    failure_type=failure_type,
                    failure_message=str(exc)[:1000],
                    template_name=template_name,
                    prompt_full=prompt_full,
                    input_meta=input_meta,
                    attempts=attempts,
                    total_duration_ms=int((time.monotonic() - started) * 1000),
                )
                raise

            assert delay is not None
            logger.warning(
                "llm_retry [%s] attempt %d/%d failed (%s), retrying in %.1fs",
                template_name, attempt + 1, max_attempts,
                type(exc).__name__, delay,
            )
            await asyncio.sleep(delay)

    raise RuntimeError("with_retry exhausted")  # unreachable


async def _write_failure(
    db,
    namespace_id: int | None,
    repo_id: int | None,
    extraction_kind: str,
    *,
    failure_type: str,
    failure_message: str,
    template_name: str,
    prompt_full: str,
    input_meta: dict[str, Any],
    attempts: list[dict[str, Any]],
    total_duration_ms: int,
) -> None:
    """Write ExtractionFailureLog with full retry history."""
    if db is None or namespace_id is None:
        return

    from app.knowledge.explain_gate import write_extraction_failure

    extra: dict[str, Any] = {
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "prompt_template_name": template_name,
        "input_meta": input_meta,
        "retry_attempts": attempts,
        "total_duration_ms": total_duration_ms,
    }
    if settings.llm_retry_log_full_prompt:
        extra["prompt_full"] = prompt_full

    await write_extraction_failure(
        db,
        namespace_id=namespace_id,
        repo_id=repo_id,
        extraction_kind=extraction_kind,
        failure_type=failure_type,
        failure_message=failure_message,
        failure_extra=extra,
    )
