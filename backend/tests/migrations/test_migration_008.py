"""migration_008 测试 — terminology_conflicts + partial unique index + 历史孤儿表清理.

PostgreSQL 真实数据库, 不 mock; 验证幂等 / 唯一索引强制 / superseded 不参与索引.

2026-05-11: knowledge_audit_log (单数) 由旧迁移建出但代码侧从未使用 (真实表是 SQLAlchemy
自动建的 knowledge_audit_logs 复数, KnowledgeAuditLog model). 改为 DROP + 测试反向断言.
"""

import os

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.schema_migrations import run_all

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/intelligent_statistics_test",
)


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(TEST_DATABASE_URL)
    async with eng.begin() as conn:
        # ── 最小依赖表 ──────────────────────────────────────────────────────────
        # run_all 会 ALTER 多张老表 + DROP namespace_rules / business_terms,
        # 全部预建为最小骨架以便 information_schema + ADD COLUMN 通过.
        await conn.execute(text("DROP TABLE IF EXISTS knowledge_audit_log CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS namespace_rules CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS knowledge_entries CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS mongo_canonical_collections CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS git_repos CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS namespaces CASCADE"))

        await conn.execute(text("""
            CREATE TABLE namespaces (id SERIAL PRIMARY KEY, slug TEXT)
        """))
        await conn.execute(text("""
            CREATE TABLE users (id SERIAL PRIMARY KEY)
        """))
        await conn.execute(text("""
            CREATE TABLE git_repos (id SERIAL PRIMARY KEY)
        """))
        await conn.execute(text("""
            CREATE TABLE mongo_canonical_collections (id SERIAL PRIMARY KEY)
        """))
        await conn.execute(text("""
            CREATE TABLE knowledge_entries (
                id SERIAL PRIMARY KEY,
                namespace_id INTEGER,
                entry_type VARCHAR(32),
                payload TEXT,
                is_superseded BOOLEAN DEFAULT FALSE
            )
        """))
        # _drop_namespace_rules_table 会 DROP IF EXISTS, 提前建出验证 DROP 行为.
        await conn.execute(text("""
            CREATE TABLE namespace_rules (id SERIAL PRIMARY KEY)
        """))
        # 模拟历史遗留: 旧迁移建出的孤儿单数表 (本次迁移应清掉).
        await conn.execute(text("""
            CREATE TABLE knowledge_audit_log (id SERIAL PRIMARY KEY)
        """))
    yield eng
    await eng.dispose()


@pytest.mark.asyncio
async def test_migration_008_drops_legacy_audit_log_idempotent(engine):
    await run_all(engine)
    await run_all(engine)  # 第二次必须不报错 (幂等)
    async with engine.connect() as conn:
        rows = await conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename IN ('knowledge_audit_log','terminology_conflicts','namespace_rules')"
        ))
        names = {r[0] for r in rows.all()}
    assert "knowledge_audit_log" not in names  # 历史孤儿单数表已 DROP
    assert "terminology_conflicts" in names
    assert "namespace_rules" not in names  # DROP


@pytest.mark.asyncio
async def test_migration_008_partial_unique_index_enforces(engine):
    await run_all(engine)
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO knowledge_entries (namespace_id, entry_type, payload, is_superseded) "
            "VALUES (1, 'terminology', "
            "'{\"primary_collection\":\"c_category\",\"primary_database\":\"db1\",\"db_type\":\"mongodb\"}', false)"
        ))
        with pytest.raises(Exception):
            await conn.execute(text(
                "INSERT INTO knowledge_entries (namespace_id, entry_type, payload, is_superseded) "
                "VALUES (1, 'terminology', "
                "'{\"primary_collection\":\"c_category\",\"primary_database\":\"db1\",\"db_type\":\"mongodb\"}', false)"
            ))


@pytest.mark.asyncio
async def test_migration_008_partial_unique_allows_superseded(engine):
    await run_all(engine)
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO knowledge_entries (namespace_id, entry_type, payload, is_superseded) "
            "VALUES (1, 'terminology', "
            "'{\"primary_collection\":\"c_category\",\"primary_database\":\"db1\",\"db_type\":\"mongodb\"}', true)"
        ))
        await conn.execute(text(
            "INSERT INTO knowledge_entries (namespace_id, entry_type, payload, is_superseded) "
            "VALUES (1, 'terminology', "
            "'{\"primary_collection\":\"c_category\",\"primary_database\":\"db1\",\"db_type\":\"mongodb\"}', false)"
        ))  # superseded=true 不参与索引, 不冲突
