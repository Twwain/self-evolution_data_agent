"""测试库 schema 对齐 — 所有 conftest 的 _engine 夹具共用。

create_all 只建缺失的表, 不会给已存在的旧表补列。测试库长期复用, 旧表可能缺
新增列 (如 namespaces.name/description、agent_traces.session_id), 导致 INSERT
报 UndefinedColumnError。本模块逐表 ADD COLUMN IF NOT EXISTS 对齐, 不删数据、
幂等、自动覆盖未来新增列。

各子包 conftest 的 _engine 必须在 create_all 后调用 reconcile_missing_columns,
否则该子包测试在旧测试库上会因列缺失而失败 (单点收口, 防多份维护漂移)。
"""
from __future__ import annotations

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models.base import Base


async def prepare_test_schema(engine: AsyncEngine) -> None:
    """测试库 schema 准备单点收口 — create_all + 列对齐 + 生产 migrations.

    三步缺一不可:
    1. create_all: 建缺失的表
    2. reconcile_missing_columns: 给旧表补缺列
    3. schema_migrations.run_all: 跑生产同款迁移 (含索引重建等 create_all 覆盖不到的
       DDL, 如 migration_018 的 partial unique 索引修复)。否则测试库索引/约束会与
       生产漂移 (实证: uq_one_open_conflict_per_field 在测试库被建成全表 unique,
       resolved 行占位阻挡同字段重开 conflict, 致 promote 撞 UniqueViolation)。

    所有 conftest 的 engine fixture 必须调用本函数, 而非各自手写前两步, 防漂移。
    """
    from app.db.schema_migrations import run_all

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(reconcile_missing_columns)
    await run_all(engine)


def reconcile_missing_columns(sync_conn) -> None:
    """对每个模型表, 把 model 有但 DB 缺的列 ALTER TABLE ADD COLUMN 补上 (Postgres).

    用法: async with engine.begin() as conn:
              await conn.run_sync(Base.metadata.create_all)
              await conn.run_sync(reconcile_missing_columns)
    """
    inspector = sa_inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())
    dialect = sync_conn.dialect

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all 已建新表, 列齐全
        live_cols = {c["name"] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name in live_cols:
                continue
            col_type = col.type.compile(dialect=dialect)
            ddl = (
                f'ALTER TABLE "{table.name}" '
                f'ADD COLUMN IF NOT EXISTS "{col.name}" {col_type}'
            )
            sync_conn.execute(text(ddl))
