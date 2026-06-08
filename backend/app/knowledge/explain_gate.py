"""EXPLAIN 闸门 — asyncio.Semaphore 限并发 + 单次超时 + 错误分类.

设计: docs/superpowers/specs/2026-05-15-schema-knowledge-onboarding/03-extraction.md §3.4.2
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.extraction_failure_log import ExtractionFailureLog


# ── Error classification patterns ──────────────────────────────────────
_ERROR_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Table '.*' doesn't exist", re.IGNORECASE), "unknown_table"),
    (re.compile(r"Unknown column", re.IGNORECASE), "unknown_column"),
    (re.compile(r"You have an error in your SQL syntax", re.IGNORECASE), "syntax_error"),
    (re.compile(r"syntax error", re.IGNORECASE), "syntax_error"),
]


def classify_mysql_error(msg: str) -> str:
    """Parse MySQL error message and return failure_type."""
    for pattern, failure_type in _ERROR_PATTERNS:
        if pattern.search(msg):
            return failure_type
    return "connection_error"


# ── Data classes ───────────────────────────────────────────────────────


@dataclass
class ExplainResult:
    ok: bool
    failure_type: str | None = None
    message: str | None = None


# ── ExplainGate ────────────────────────────────────────────────────────


class ExplainGate:
    """asyncio.Semaphore 限并发 + 单次超时 + 错误分类."""

    def __init__(
        self,
        ds,
        *,
        concurrency: int | None = None,
        timeout_secs: int | None = None,
    ):
        self._sem = asyncio.Semaphore(concurrency or settings.explain_gate_concurrency)
        self._ds = ds
        self._timeout = timeout_secs or settings.explain_gate_timeout_secs

    async def check(self, sql: str) -> ExplainResult:
        """Run EXPLAIN on the SQL. Returns ExplainResult."""
        async with self._sem:
            try:
                async with asyncio.timeout(self._timeout):
                    return await self._explain(sql)
            except asyncio.TimeoutError:
                return ExplainResult(
                    ok=False,
                    failure_type="explain_timeout",
                    message="EXPLAIN timeout",
                )

    async def _explain(self, sql: str) -> ExplainResult:
        """Actually run EXPLAIN ... LIMIT 0 on the datasource connection."""
        try:
            conn = await self._ds.get_connection()
            try:
                await conn.execute(f"EXPLAIN {sql} LIMIT 0")
            finally:
                await conn.close()
            return ExplainResult(ok=True)
        except Exception as exc:
            msg = str(exc)
            failure_type = classify_mysql_error(msg)
            return ExplainResult(ok=False, failure_type=failure_type, message=msg)

    async def check_batch(self, sqls: list[str]) -> list[ExplainResult]:
        """Check multiple SQLs with concurrency control."""
        return await asyncio.gather(*[self.check(sql) for sql in sqls])

    async def check_and_log(
        self,
        sql: str,
        *,
        db: "AsyncSession",
        namespace_id: int,
        extraction_kind: str,
        repo_id: int | None = None,
        source_mapper: str | None = None,
        source_method: str | None = None,
    ) -> ExplainResult:
        """Check + auto-log failures.

        connection_error 不入 log (datasource 基础设施问题, 与抽取产物无关).
        explain_timeout 入 log (设计修订: timeout 是抽取链路可观测信号, 不可静默丢失).
        """
        result = await self.check(sql)
        if not result.ok and result.failure_type != "connection_error":
            await write_extraction_failure(
                db,
                namespace_id=namespace_id,
                repo_id=repo_id,
                datasource_id=getattr(self._ds, "id", None),
                extraction_kind=extraction_kind,
                source_mapper=source_mapper,
                source_method=source_method,
                source_content=sql,
                failure_type=result.failure_type or "syntax_error",
                failure_message=result.message or "",
            )
        return result


# ── ExtractionFailureLog writer ────────────────────────────────────────


async def write_extraction_failure(
    db: AsyncSession,
    *,
    namespace_id: int,
    repo_id: int | None = None,
    datasource_id: int | None = None,
    extraction_kind: str,
    source_file: str | None = None,
    source_mapper: str | None = None,
    source_method: str | None = None,
    source_content: str | None = None,
    failure_type: str,
    failure_message: str,
    failure_extra: dict | None = None,
) -> int:
    """Write to ExtractionFailureLog. Returns log id."""
    log = ExtractionFailureLog(
        namespace_id=namespace_id,
        repo_id=repo_id,
        datasource_id=datasource_id,
        extraction_kind=extraction_kind,
        source_file=source_file,
        source_mapper=source_mapper,
        source_method=source_method,
        source_content=source_content,
        failure_type=failure_type,
        failure_message=failure_message,
        failure_extra_json=json.dumps(failure_extra) if failure_extra else None,
    )
    db.add(log)
    await db.flush()
    return log.id
