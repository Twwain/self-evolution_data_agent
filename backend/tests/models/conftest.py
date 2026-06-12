"""Phase 1a Task 1.2 — tests/models/ 专用 fixtures.

提供 sessionmaker 形态的 ``async_session`` (与 tests/integration/conftest.py 同形态,
但简化掉对 ``app.db.metadata.async_session`` 的 monkeypatch — 模型层测试不依赖
生产 sessionmaker 替身), 以及 ``seeded_ns_and_ke`` 一键种入 Namespace + 一条
canonical terminology KnowledgeEntry, 给 TerminologyConflict 外键挂载用.
"""

import json
import os

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import KnowledgeEntry, Namespace
from app.models.base import Base
from tests._db_schema_sync import prepare_test_schema

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent_test",
)


# ════════════════════════════════════════════
#  async_session — sessionmaker 形态, PostgreSQL
# ════════════════════════════════════════════
@pytest_asyncio.fixture
async def async_session():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_timezone(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET timezone = 'Asia/Shanghai'")
        cursor.close()

    await prepare_test_schema(engine)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


# ════════════════════════════════════════════
#  seeded_ns_and_ke — 1 Namespace + 1 canonical terminology KE
# ════════════════════════════════════════════
@pytest_asyncio.fixture
async def seeded_ns_and_ke(async_session) -> tuple[int, int]:
    async with async_session() as db:
        ns = Namespace(name="phase1a_test", slug="phase1a_test", description="task 1.2")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        ke = KnowledgeEntry(
            namespace_id=ns.id,
            entry_type="terminology",
            source="manual",
            status="canonical",
            is_superseded=False,
            payload=json.dumps(
                {
                    "term": "订单",
                    "primary_collection": "c_product",
                    "primary_database": "db_q",
                    "db_type": "mongodb",
                }
            ),
            content="订单",
        )
        db.add(ke)
        await db.commit()
        await db.refresh(ke)

        return ns.id, ke.id
