"""
conftest — 共享 fixture：PostgreSQL 测试数据库、模型表
"""

import asyncio
import os

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from tests._db_schema_sync import reconcile_missing_columns

# 测试数据库 URL — 从环境变量读取，默认使用远程测试 RDS
TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/intelligent_statistics_test",
)


# ── pytest-asyncio 全局配置 ──
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def _engine():
    """Session-scoped async engine for PostgreSQL test database."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    # 连接级时区设置 — 与生产一致
    @event.listens_for(engine.sync_engine, "connect")
    def _set_timezone(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET timezone = 'Asia/Shanghai'")
        cursor.close()

    # 建表（幂等）+ 列级 schema 对齐 (旧表补缺列, 见 _db_schema_sync)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(reconcile_missing_columns)

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db(_engine):
    """Per-test PostgreSQL session with transaction rollback isolation.

    每个测试在一个事务内运行，测试结束后回滚，保证测试间隔离且速度快。
    使用 SAVEPOINT 嵌套事务，使得测试内的 commit() 调用不会真正提交。
    """
    async with _engine.connect() as conn:
        trans = await conn.begin()
        # 创建嵌套事务（SAVEPOINT），使得 session.commit() 只提交到 savepoint
        nested = await conn.begin_nested()

        session = AsyncSession(bind=conn, expire_on_commit=False)

        # 当 session.commit() 被调用时，重新开始一个新的 SAVEPOINT
        @event.listens_for(session.sync_session, "after_transaction_end")
        def _restart_savepoint(sync_session, transaction):
            if transaction.nested and not transaction._parent.nested:
                sync_session.begin_nested()

        yield session

        await session.close()
        await trans.rollback()


@pytest_asyncio.fixture
async def admin_client(db):
    """ASGI client 自动以假 admin 身份认证, 复用测试 db 会话"""
    from httpx import AsyncClient, ASGITransport

    from app.main import app
    from app.auth import get_current_user, require_admin
    from app.db.metadata import get_db
    from app.models.user import User

    async def _fake_admin():
        return User(id=1, username="admin", role="admin", password_hash="x")

    async def _fake_db():
        yield db

    app.dependency_overrides[require_admin] = _fake_admin
    app.dependency_overrides[get_current_user] = _fake_admin
    app.dependency_overrides[get_db] = _fake_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
