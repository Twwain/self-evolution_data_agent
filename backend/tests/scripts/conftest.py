"""scripts 测试共享 fixture — SAVEPOINT rollback 隔离.

提供 `async_session` 工厂 (兼容 `async with async_session() as db: ...` 语法),
底层走 SAVEPOINT rollback, 保证测试隔离.
"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.models.base import Base
from app.models.namespace import Namespace
from tests._db_schema_sync import prepare_test_schema

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/intelligent_statistics_test",
)


@pytest_asyncio.fixture
async def _engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_timezone(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET timezone = 'Asia/Shanghai'")
        cursor.close()

    await prepare_test_schema(engine)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Per-test session with SAVEPOINT rollback."""
    async with _engine.connect() as conn:
        trans = await conn.begin()
        await conn.begin_nested()

        session = AsyncSession(bind=conn, expire_on_commit=False)

        @event.listens_for(session.sync_session, "after_transaction_end")
        def _restart_savepoint(sync_session, transaction):
            if transaction.nested and not transaction._parent.nested:
                sync_session.begin_nested()

        yield session

        await session.close()
        await trans.rollback()


@pytest.fixture
def async_session(db_session):
    """sessionmaker 形态别名: `async with async_session() as db: ...`."""
    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


async def _ensure_ns(db: AsyncSession, *, slug: str = "test_ns") -> Namespace:
    """种入或复用一条 Namespace, 供 KnowledgeEntry FK 锚定."""
    existing = (await db.execute(select(Namespace).where(Namespace.slug == slug))).scalar_one_or_none()
    if existing is not None:
        return existing
    ns = Namespace(name=slug, slug=slug, description="phase0 relabel test")
    db.add(ns)
    await db.commit()
    await db.refresh(ns)
    return ns
