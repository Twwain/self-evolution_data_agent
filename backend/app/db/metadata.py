"""
元数据库 — PostgreSQL async session
单一数据源, 拒绝全局变量污染
"""

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.metadata_db_url,
    echo=False,
    pool_size=settings.metadata_pool_size,
    max_overflow=settings.metadata_pool_max_overflow,
    pool_timeout=settings.metadata_pool_timeout_secs,
    pool_pre_ping=True,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_timezone(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("SET timezone = 'Asia/Shanghai'")
    cursor.close()


async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:  # type: ignore[misc, return-type]
    async with async_session() as session:
        yield session  # type: ignore[misc]
