"""共享 helper: 按 (namespace_id, db_type, database) 反查 DataSource.

工具层统一入口 — LLM 传三件套 (db_type, database, target), 工具层用此函数
反查出 DataSource 行, 再交给 driver 执行. LLM 不感知 datasource_id.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DataSource


async def resolve_ds(
    db: AsyncSession,
    namespace_id: int,
    db_type: str,
    database: str,
) -> DataSource | None:
    """按 (namespace_id, db_type, database) 反查 DataSource. 无匹配返 None."""
    stmt = (
        select(DataSource)
        .where(
            DataSource.namespace_id == namespace_id,
            DataSource.db_type == db_type,
            DataSource.database == database,
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
