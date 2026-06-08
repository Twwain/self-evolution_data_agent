import os

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, DataSource, GitRepo, Namespace  # noqa: F401 — side-effect: registers all models in metadata
import app.knowledge.equivalence.checkers  # noqa: F401 — side-effect: register equivalence rules
from tests._db_schema_sync import reconcile_missing_columns

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

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(reconcile_missing_columns)
    yield engine  # type: ignore[misc]
    await engine.dispose()


@pytest_asyncio.fixture
async def test_session(test_engine: AsyncEngine) -> AsyncSession:
    Session = async_sessionmaker(test_engine, expire_on_commit=False)
    async with Session() as session:
        yield session  # type: ignore[misc]


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
