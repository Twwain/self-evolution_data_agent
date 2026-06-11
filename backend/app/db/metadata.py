"""
元数据库 — PostgreSQL async session
单一数据源, 拒绝全局变量污染
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.metadata_db_url,
    echo=False,
    pool_size=settings.metadata_pool_size,
    max_overflow=settings.metadata_pool_max_overflow,
    pool_timeout=settings.metadata_pool_timeout_secs,
    pool_pre_ping=True,
    # asyncpg 在每条连接建立时强制下发 timezone，覆盖所有连接（含池扩张/重连）。
    # 取代旧的 connect 事件监听器（async 引擎下触发不稳定，曾导致部分连接仍为 UTC，
    # 使裸 DEFAULT NOW() 列偶发写成 UTC、与本地时间混杂）。
    connect_args={"server_settings": {"timezone": "Asia/Shanghai"}},
)


async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:  # type: ignore[misc, return-type]
    async with async_session() as session:
        yield session  # type: ignore[misc]
