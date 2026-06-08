"""ExplainGate + write_extraction_failure 单元测试.

设计: docs/superpowers/specs/2026-05-15-schema-knowledge-onboarding/03-extraction.md §3.4.2
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.explain_gate import (
    ExplainGate,
    ExplainResult,
    classify_mysql_error,
    write_extraction_failure,
)
from app.models.extraction_failure_log import ExtractionFailureLog


# ── Helpers ────────────────────────────────────────────────────────────


def _make_ds(*, execute_side_effect=None):
    """Create a mock datasource with async get_connection."""
    conn = AsyncMock()
    if execute_side_effect:
        conn.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        conn.execute = AsyncMock(return_value=None)
    conn.close = AsyncMock()

    ds = MagicMock()
    ds.get_connection = AsyncMock(return_value=conn)
    return ds


# ── classify_mysql_error ───────────────────────────────────────────────


class TestClassifyMysqlError:
    def test_unknown_table(self):
        msg = "1146 (42S02): Table 'db.orders' doesn't exist"
        assert classify_mysql_error(msg) == "unknown_table"

    def test_unknown_column(self):
        msg = "1054 (42S22): Unknown column 'foo' in 'field list'"
        assert classify_mysql_error(msg) == "unknown_column"

    def test_syntax_error(self):
        msg = "1064 (42000): You have an error in your SQL syntax"
        assert classify_mysql_error(msg) == "syntax_error"

    def test_fallback_connection_error(self):
        msg = "2003: Can't connect to MySQL server"
        assert classify_mysql_error(msg) == "connection_error"


# ── ExplainGate.check ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_explain_gate_passes_valid_sql():
    """EXPLAIN 成功 → ExplainResult(ok=True)."""
    ds = _make_ds()
    gate = ExplainGate(ds, concurrency=2, timeout_secs=5)

    result = await gate.check("SELECT 1")

    assert result.ok is True
    assert result.failure_type is None


@pytest.mark.asyncio
async def test_explain_gate_fails_unknown_table():
    """EXPLAIN 报 unknown table → failure_type='unknown_table'."""
    ds = _make_ds(
        execute_side_effect=Exception(
            "1146 (42S02): Table 'db.orders' doesn't exist"
        )
    )
    gate = ExplainGate(ds, concurrency=2, timeout_secs=5)

    result = await gate.check("SELECT * FROM orders")

    assert result.ok is False
    assert result.failure_type == "unknown_table"
    assert "doesn't exist" in result.message


@pytest.mark.asyncio
async def test_explain_gate_timeout():
    """EXPLAIN 超时 → failure_type='connection_error', message 含 timeout."""

    async def _slow_execute(*args, **kwargs):
        await asyncio.sleep(10)

    ds = _make_ds()
    ds.get_connection.return_value.execute = AsyncMock(side_effect=_slow_execute)

    gate = ExplainGate(ds, concurrency=2, timeout_secs=0.05)

    result = await gate.check("SELECT SLEEP(100)")

    assert result.ok is False
    assert result.failure_type == "connection_error"
    assert "timeout" in result.message.lower()


@pytest.mark.asyncio
async def test_explain_gate_concurrency_limit():
    """Semaphore 限制并发: concurrency=2 时最多 2 个同时执行."""
    max_concurrent = 0
    current_concurrent = 0
    lock = asyncio.Lock()

    async def _tracked_execute(*args, **kwargs):
        nonlocal max_concurrent, current_concurrent
        async with lock:
            current_concurrent += 1
            if current_concurrent > max_concurrent:
                max_concurrent = current_concurrent
        await asyncio.sleep(0.02)
        async with lock:
            current_concurrent -= 1

    ds = _make_ds()
    ds.get_connection.return_value.execute = AsyncMock(side_effect=_tracked_execute)

    gate = ExplainGate(ds, concurrency=2, timeout_secs=5)

    # Launch 6 concurrent checks
    await asyncio.gather(*[gate.check(f"SELECT {i}") for i in range(6)])

    assert max_concurrent <= 2


# ── ExplainGate.check_batch ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_explain_gate_batch_processes_all():
    """check_batch 处理所有 SQL 并返回对应结果列表."""
    call_count = 0

    async def _counting_execute(*args, **kwargs):
        nonlocal call_count
        call_count += 1

    ds = _make_ds()
    ds.get_connection.return_value.execute = AsyncMock(side_effect=_counting_execute)

    gate = ExplainGate(ds, concurrency=4, timeout_secs=5)

    sqls = [f"SELECT {i}" for i in range(5)]
    results = await gate.check_batch(sqls)

    assert len(results) == 5
    assert all(r.ok for r in results)
    assert call_count == 5


# ── write_extraction_failure ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_extraction_failure_creates_log(db_session: AsyncSession):
    """write_extraction_failure 写入 ExtractionFailureLog 并返回 id."""
    from app.models.namespace import Namespace

    # Seed namespace
    ns = Namespace(name="test_ef", slug="test_ef", description="test")
    db_session.add(ns)
    await db_session.flush()

    log_id = await write_extraction_failure(
        db_session,
        namespace_id=ns.id,
        extraction_kind="mybatis_example",
        source_file="OrderMapper.xml",
        source_mapper="com.example.OrderMapper",
        source_method="selectById",
        source_content="SELECT * FROM orders WHERE id = #{id}",
        failure_type="unknown_table",
        failure_message="Table 'db.orders' doesn't exist",
        failure_extra={"sql": "SELECT * FROM orders WHERE id = 1"},
    )

    assert log_id > 0

    # Verify persisted
    row = await db_session.get(ExtractionFailureLog, log_id)
    assert row is not None
    assert row.namespace_id == ns.id
    assert row.extraction_kind == "mybatis_example"
    assert row.failure_type == "unknown_table"
    assert row.source_mapper == "com.example.OrderMapper"
    assert '"sql"' in row.failure_extra_json


# ── ExplainGate.check_and_log ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_gate_failure_writes_extraction_log(db_session: AsyncSession):
    """unknown_table 等失败入 ExtractionFailureLog (connection_error 不入)."""
    from app.models.namespace import Namespace

    ns = Namespace(name="test_gate_log", slug="test_gate_log", description="test")
    db_session.add(ns)
    await db_session.flush()

    ds = _make_ds(
        execute_side_effect=Exception(
            "1146 (42S02): Table 'db.t_x' doesn't exist"
        )
    )
    ds.id = 999  # mock datasource id

    gate = ExplainGate(ds, concurrency=4, timeout_secs=5)
    result = await gate.check_and_log(
        "SELECT * FROM t_x",
        db=db_session,
        namespace_id=ns.id,
        extraction_kind="mybatis_example",
        source_mapper="OrderMapper",
        source_method="m1",
    )

    assert result.ok is False
    assert result.failure_type == "unknown_table"

    logs = (await db_session.execute(select(ExtractionFailureLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].failure_type == "unknown_table"
    assert logs[0].source_mapper == "OrderMapper"
    assert logs[0].extraction_kind == "mybatis_example"


@pytest.mark.asyncio
async def test_gate_connection_error_not_logged(db_session: AsyncSession):
    """connection_error 单列 — 不入 ExtractionFailureLog."""
    from app.models.namespace import Namespace

    ns = Namespace(name="test_gate_conn", slug="test_gate_conn", description="test")
    db_session.add(ns)
    await db_session.flush()

    async def _slow_execute(*args, **kwargs):
        await asyncio.sleep(10)

    ds = _make_ds()
    ds.get_connection.return_value.execute = AsyncMock(side_effect=_slow_execute)
    ds.id = 998

    gate = ExplainGate(ds, concurrency=4, timeout_secs=0.05)
    result = await gate.check_and_log(
        "SELECT 1",
        db=db_session,
        namespace_id=ns.id,
        extraction_kind="mybatis_example",
    )

    assert result.ok is False
    assert result.failure_type == "connection_error"

    logs = (await db_session.execute(select(ExtractionFailureLog))).scalars().all()
    assert logs == []
