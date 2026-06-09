"""SchemaCanonicalObject CRUD — 跨数据库 schema 真相源读写.

公开函数:
- get_schema_canonical: 按 (ns_id, db_type, database, target) 查单条
- upsert_schema_canonical: 幂等写入 (存在则更新, 不存在则插入)
- list_schema_canonicals: 按 namespace 列出全部
- refresh_mysql_canonicals: 从 MySQLDriver introspect 刷新 namespace 下所有 MySQL 表
- backfill_indexes_from_driver: 从 driver introspect 补充 SCO 的 indexes_json + field indexed
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SchemaCanonicalObject

log = logging.getLogger(__name__)


async def get_schema_canonical(
    db: AsyncSession,
    namespace_id: int,
    db_type: str,
    database: str,
    target: str,
) -> SchemaCanonicalObject | None:
    """按四元组查单条 canonical. 无匹配返 None."""
    stmt = select(SchemaCanonicalObject).where(
        SchemaCanonicalObject.namespace_id == namespace_id,
        SchemaCanonicalObject.db_type == db_type,
        SchemaCanonicalObject.database == database,
        SchemaCanonicalObject.target == target,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_schema_canonical(
    db: AsyncSession,
    *,
    namespace_id: int,
    db_type: str,
    database: str,
    target: str,
    fields_json: str = "[]",
    indexes_json: str = "[]",
    description: str = "",
    purpose_detail: str = "",
    sample_count: int = 0,
    source: str = "introspect",
) -> SchemaCanonicalObject:
    """幂等写入 — 存在则更新, 不存在则插入."""
    existing = await get_schema_canonical(db, namespace_id, db_type, database, target)

    if existing:
        existing.fields_json = fields_json
        existing.indexes_json = indexes_json
        existing.description = description
        existing.purpose_detail = purpose_detail
        existing.sample_count = sample_count
        existing.source = source
        existing.updated_at = datetime.now()
        await db.commit()
        return existing

    obj = SchemaCanonicalObject(
        namespace_id=namespace_id,
        db_type=db_type,
        database=database,
        target=target,
        fields_json=fields_json,
        indexes_json=indexes_json,
        description=description,
        purpose_detail=purpose_detail,
        sample_count=sample_count,
        source=source,
    )
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def list_schema_canonicals(
    db: AsyncSession,
    namespace_id: int,
    db_type: str | None = None,
) -> list[SchemaCanonicalObject]:
    """列出 namespace 下全部 canonical, 可选按 db_type 过滤."""
    stmt = select(SchemaCanonicalObject).where(
        SchemaCanonicalObject.namespace_id == namespace_id,
    )
    if db_type:
        stmt = stmt.where(SchemaCanonicalObject.db_type == db_type)
    stmt = stmt.order_by(SchemaCanonicalObject.db_type, SchemaCanonicalObject.target)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def refresh_mysql_canonicals(
    db: AsyncSession,
    namespace_id: int,
    ns_slug: str,
    referenced_tables: set[str] | None = None,
    repo_name: str = "",
    trigger_promote: bool = True,
) -> int:
    """从 MySQLDriver introspect 写入 candidate 层 (不再直写 SchemaCanonicalObject).

    Args:
        referenced_tables: 仅 introspect 集合内的表名 (per-repo 范围收窄).
            None → 全表 introspect (手动按钮路径保留旧语义).
            空 set → 早返 0 (本 repo 无 mysql 引用, noop).
        trigger_promote: 写完 candidate 后是否内部触发 promote + 索引补充.
            True (默认, 手动刷新端点用) → 自给自足汇聚, 因端点无后续汇聚步骤.
            False (trainer step 6.4 用) → 只写候选, 汇聚交给 step 6.5 的
            maybe_trigger_promote 统一处理, 与 MongoDB 候选路径对齐, 避免
            同一训练内重复全量 promote (历史冗余, 见 stage-b spec).

    返回处理的表数量 (backward compat). 调用方负责 commit.
    """
    from sqlalchemy import select as sa_select

    from app.engine.drivers import get_driver
    from app.knowledge.canonical_introspect import write_introspect_candidates_for_target
    from app.knowledge.canonical_promote import promote_candidates_to_canonical
    from app.models import DataSource

    if referenced_tables is not None and not referenced_tables:
        return 0

    ds_rows = (await db.execute(
        sa_select(DataSource).where(
            DataSource.namespace_id == namespace_id,
            DataSource.db_type == "mysql",
        )
    )).scalars().all()

    if not ds_rows:
        return 0

    driver = get_driver("mysql")
    table_count = 0

    for ds in ds_rows:
        try:
            schemas = await driver.fetch_schema(ds, target=None)
            if not isinstance(schemas, list):
                continue

            for table_stub in schemas:
                target = table_stub["target"]
                if referenced_tables is not None and target not in referenced_tables:
                    continue
                detail = await driver.fetch_schema(ds, target=target)
                if isinstance(detail, list):
                    continue

                await write_introspect_candidates_for_target(
                    db,
                    namespace_id=namespace_id,
                    db_type="mysql",
                    database=ds.database,
                    target=detail["target"],
                    detail=cast("dict[str, Any]", detail),
                    datasource_id=ds.id,
                )
                table_count += 1

        except Exception as e:
            log.warning(
                "[%s] MySQL introspect failed ds=%d: %s",
                repo_name, ds.id, e,
            )

    # 写完所有 candidate 后触发 promote (仅 trigger_promote=True)
    if table_count > 0 and trigger_promote:
        await promote_candidates_to_canonical(db, namespace_id)
        # promote 后补充索引信息 (indexes_json + field indexed)
        await backfill_indexes_from_driver(db, namespace_id, db_type="mysql")

    log.info(
        "[%s] refreshed %d MySQL tables (via candidate) for ns=%d",
        repo_name, table_count, namespace_id,
    )
    return table_count


async def backfill_indexes_from_driver(
    db: AsyncSession,
    namespace_id: int,
    db_type: str | None = None,
) -> int:
    """从 driver introspect 补充 SCO 的 indexes_json + field 级 indexed 标记.

    遍历 namespace 下所有 SCO, 对每个 target 调 driver.fetch_schema 拿索引,
    写入 sco.indexes_json 并标记 fields_json 中对应字段的 indexed=True.

    Args:
        db_type: 可选, 仅处理指定 db_type 的 SCO. None → 全部.

    Returns:
        更新的 SCO 数量.
    """
    import json

    from app.engine.drivers import get_driver
    from app.models import DataSource

    scos = await list_schema_canonicals(db, namespace_id, db_type=db_type)
    if not scos:
        return 0

    # 按 (db_type, database) 分组查 DataSource
    ds_cache: dict[tuple[str, str], DataSource | None] = {}

    async def _get_ds(sco_db_type: str, sco_database: str) -> DataSource | None:
        key = (sco_db_type, sco_database)
        if key not in ds_cache:
            from sqlalchemy import select as sa_select
            row = (await db.execute(
                sa_select(DataSource).where(
                    DataSource.namespace_id == namespace_id,
                    DataSource.db_type == sco_db_type,
                    DataSource.database == sco_database,
                )
            )).scalar_one_or_none()
            ds_cache[key] = row
        return ds_cache[key]

    updated = 0
    for sco in scos:
        ds = await _get_ds(sco.db_type, sco.database)
        if ds is None:
            continue

        try:
            driver = get_driver(sco.db_type)
            schema = await driver.fetch_schema(ds, sco.target)
            if isinstance(schema, list):
                continue

            indexes = schema.get("indexes", [])
            if not indexes:
                continue

            # 写入 indexes_json
            sco.indexes_json = json.dumps(indexes, ensure_ascii=False)

            # 从 indexes 提取有索引的字段名集合
            indexed_fields: set[str] = set()
            for idx in indexes:
                # MySQL: {"columns": ["col1", "col2"]}
                # MongoDB: {"keys": {"field1": 1, "field2": -1}}
                cols = idx.get("columns") or []
                keys = idx.get("keys") or {}
                for col in cols:
                    indexed_fields.add(col)
                for k in keys:
                    indexed_fields.add(k)

            # 标记 fields_json 中对应字段的 indexed
            fields = json.loads(sco.fields_json or "[]")
            changed = False
            for f in fields:
                name = f.get("name", "")
                should_be_indexed = name in indexed_fields
                if f.get("indexed") != should_be_indexed and should_be_indexed:
                    f["indexed"] = True
                    changed = True
            if changed:
                sco.fields_json = json.dumps(fields, ensure_ascii=False)

            sco.updated_at = datetime.now()
            updated += 1

        except Exception as e:
            log.warning(
                "backfill_indexes failed sco=%d target=%s: %s",
                sco.id, sco.target, e,
            )

    if updated:
        await db.flush()

    log.info(
        "backfill_indexes ns=%d: updated %d/%d SCOs",
        namespace_id, updated, len(scos),
    )
    return updated
