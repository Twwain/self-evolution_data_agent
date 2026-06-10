import os

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.models import Base, DataSource, GitRepo, Namespace  # noqa: F401 — side-effect: registers all models in metadata
import app.knowledge.equivalence.checkers  # noqa: F401 — side-effect: register equivalence rules
from tests._db_schema_sync import prepare_test_schema

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/intelligent_statistics_test",
)


@pytest_asyncio.fixture
async def test_engine() -> AsyncEngine:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_timezone(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET timezone = 'Asia/Shanghai'")
        cursor.close()

    await prepare_test_schema(engine)
    yield engine  # type: ignore[misc]
    await engine.dispose()


@pytest_asyncio.fixture
async def test_session(test_engine: AsyncEngine) -> AsyncSession:
    """事务回滚隔离 — 测试内 commit() / begin_nested() 仅落到 SAVEPOINT, 结束 rollback, 零残留.

    用 SQLAlchemy 2.0 的 join_transaction_mode="create_savepoint": session 绑定到
    已开启外层事务的连接, 其后所有 commit()/begin_nested() 自动在 SAVEPOINT 层面操作,
    与业务代码自用的 begin_nested() 兼容 (手写 after_transaction_end 钩子会与之冲突)。
    测试结束统一 rollback 外层事务, 不向远程库提交任何数据。
    """
    async with test_engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        yield session  # type: ignore[misc]
        await session.close()
        await trans.rollback()


@pytest_asyncio.fixture
async def namespace_factory(test_session: AsyncSession):
    counter = {"n": 0}

    async def _make(slug: str | None = None) -> Namespace:
        counter["n"] += 1
        ns = Namespace(slug=slug or f"test_ns_{counter['n']}", name=f"Test NS {counter['n']}")
        test_session.add(ns)
        await test_session.flush()
        return ns

    return _make


@pytest_asyncio.fixture
async def repo_factory(test_session: AsyncSession):
    counter = {"n": 0}

    async def _make(*, ns_id: int, url: str | None = None) -> GitRepo:
        counter["n"] += 1
        repo = GitRepo(
            namespace_id=ns_id,
            url=url or f"https://example.com/repo-{counter['n']}",
            branch="main",
            parse_status="parsed",
        )
        test_session.add(repo)
        await test_session.flush()
        return repo

    return _make


@pytest_asyncio.fixture
async def datasource_factory(test_session: AsyncSession):
    counter = {"n": 0}

    async def _make(*, ns_id: int) -> DataSource:
        counter["n"] += 1
        ds = DataSource(
            namespace_id=ns_id,
            db_type="mysql",
            host="localhost",
            port=3306,
            database=f"db_{counter['n']}",
            username="x",
            password="y",
        )
        test_session.add(ds)
        await test_session.flush()
        return ds

    return _make
