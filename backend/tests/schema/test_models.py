"""Phase 1 Task 1+2: 4 张新表 ORM 模型 smoke 测试.

仅验证表能 create_all 落库 + 列类型正确, 业务行为见后续 task.
"""
import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.asyncio


async def test_schema_canonical_candidate_table_exists(test_engine: AsyncEngine):
    async with test_engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    assert "schema_canonical_candidates" in tables


async def test_schema_canonical_conflict_table_exists(test_engine: AsyncEngine):
    async with test_engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    assert "schema_canonical_conflicts" in tables


async def test_schema_canonical_audit_log_table_exists(test_engine: AsyncEngine):
    async with test_engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    assert "schema_canonical_audit_logs" in tables


async def test_extraction_failure_log_table_exists(test_engine: AsyncEngine):
    async with test_engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    assert "extraction_failure_logs" in tables


async def test_schema_canonical_object_has_new_columns(test_engine: AsyncEngine):
    async with test_engine.connect() as conn:
        cols = await conn.run_sync(
            lambda c: {col["name"] for col in inspect(c).get_columns("schema_canonical_objects")}
        )
    assert "relationships_json" in cols
    assert "sample_values_json" in cols
    assert "user_locked" in cols
