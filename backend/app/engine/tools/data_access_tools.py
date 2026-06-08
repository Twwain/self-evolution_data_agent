"""Stage 3 多态数据访问工具 — 4 个统一抽象工具.

fetch_schema / inspect_values / estimate_cost / execute_query

设计: 02-tool-layer-contract.md § 2.2-2.4
- 所有 4 个工具必传 (db_type, database, target) 三件套
- 内部走 resolve_ds → get_driver(db_type) → driver method
- 错误统一转结构化 dict (不抛给 agent_loop)
- LLM 不感知 datasource_id

与旧工具的关系:
- fetch_schema 替代 fetch_collection_schema
- inspect_values 替代 inspect_field_values
- estimate_cost 替代 estimate_query_cost
- execute_query 合并 prequery_collection + execute_count_only + execute_batched_aggregate
"""
from __future__ import annotations

import logging
from typing import Any

from langfuse import observe
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.drivers import get_driver
from app.engine.drivers._exceptions import DriverError
from app.engine.tools._resolve_ds import resolve_ds

log = logging.getLogger(__name__)


def _ds_not_found_error(namespace_id: int, db_type: str, database: str) -> dict:
    return {
        "error": "datasource_not_found",
        "message": f"namespace {namespace_id} 下没有 {db_type}+{database} 的数据源",
        "suggestion": "调 lookup_knowledge(types=['terminology']) 看锚点是否过期",
    }


def _error_from_driver(e: DriverError) -> dict:
    return e.to_dict()


# ════════════════════════════════════════════
#  server_capabilities merge helper
# ════════════════════════════════════════════

async def _attach_server_capabilities(
    result: dict, driver: Any, ds: Any,
) -> dict:
    """Attach driver.get_server_capabilities() to result if non-None.

    Failure-safe: any exception → no field added (never block primary path).
    None → field omitted entirely (clean shape, not None placeholder).
    """
    try:
        caps = await driver.get_server_capabilities(ds)
    except Exception:  # noqa: BLE001 — capability probe must never block primary path
        return result
    if caps is not None:
        result["server_capabilities"] = caps
    return result


# ════════════════════════════════════════════
#  fetch_schema — 拉取目标表/集合的 schema
# ════════════════════════════════════════════

_INTERNAL_ENUM_KEYS = frozenset({
    "enum_ref_id",
    "enum_source",
    "enum_match_status",
    "enum_class_hint",
    "sample_values",
    "sample_metadata",
    "description_confidence",
    "indexed",
    "user_locked",
})


def _project_field_for_llm(field: dict[str, Any]) -> dict[str, Any]:
    """递归投影: 只保留 LLM 需要的字段, 过滤 UI-only 元数据.

    - 黑名单 key 全部剥离 (含递归 sub_fields)
    - enum_values: pending/conflict 时不给 LLM
    """
    out: dict[str, Any] = {}
    for k, v in field.items():
        if k in _INTERNAL_ENUM_KEYS:
            continue
        if k == "sub_fields" and isinstance(v, list):
            out[k] = [_project_field_for_llm(sf) for sf in v]
        elif k == "enum_values":
            status = field.get("enum_match_status")
            if status in ("pending", "conflict"):
                continue
            out[k] = v
        else:
            out[k] = v
    return out


# 向后兼容别名 (测试文件引用)
_filter_enum_fields_for_llm = _project_field_for_llm

@observe(name="tool.fetch_schema")
async def fetch_schema(
    *,
    db: AsyncSession,
    namespace_id: int,
    db_type: str,
    database: str,
    target: str,
) -> dict:
    """拉取目标表/集合的 schema (字段定义 + 索引).

    优先读 SchemaCanonicalObject (用户校对版), fallback driver introspect.
    """
    ds = await resolve_ds(db, namespace_id, db_type, database)
    if ds is None:
        return _ds_not_found_error(namespace_id, db_type, database)

    # Stage 2: 优先读 SchemaCanonicalObject (用户校对版)
    from app.knowledge.schema_canonical import get_schema_canonical
    import json as _json

    canonical = await get_schema_canonical(db, namespace_id, db_type, database, target)
    if canonical:
        try:
            fields = _json.loads(canonical.fields_json or "[]")
            indexes = _json.loads(canonical.indexes_json or "[]")
        except _json.JSONDecodeError:
            fields, indexes = [], []
        fields = [_project_field_for_llm(f) for f in fields]
        result = {
            "db_type": db_type,
            "database": database,
            "target": target,
            "description": canonical.description,
            "fields": fields,
            "indexes": indexes,
            "relationships": _json.loads(canonical.relationships_json or "[]"),
            "sample_count": canonical.sample_count,
            "source": "canonical",
        }
        driver = get_driver(db_type)
        return await _attach_server_capabilities(result, driver, ds)

    # Fallback: driver 实时 introspect
    try:
        driver = get_driver(db_type)
        schema = await driver.fetch_schema(ds, target)
        if isinstance(schema, list):
            return {"error": "invalid_target", "message": "target 不能为空"}
        result: dict[str, Any] = {**schema, "relationships": [], "source": "introspect"}
        result["fields"] = [_project_field_for_llm(f) for f in result.get("fields", [])]
        return await _attach_server_capabilities(result, driver, ds)
    except DriverError as e:
        return _error_from_driver(e)


# ════════════════════════════════════════════
#  inspect_values — 字段值分布探查
# ════════════════════════════════════════════

@observe(name="tool.inspect_values")
async def inspect_values(
    *,
    db: AsyncSession,
    namespace_id: int,
    db_type: str,
    database: str,
    target: str,
    field: str,
    limit: int = 10,
) -> dict:
    """探目标字段的 distinct 值分布 (默认 top 10)."""
    ds = await resolve_ds(db, namespace_id, db_type, database)
    if ds is None:
        return _ds_not_found_error(namespace_id, db_type, database)

    try:
        driver = get_driver(db_type)
        values = await driver.inspect_values(ds, target, field, limit)
        return {"values": values, "field": field, "target": target}
    except DriverError as e:
        return _error_from_driver(e)


# ════════════════════════════════════════════
#  estimate_cost — 预估查询代价
# ════════════════════════════════════════════

@observe(name="tool.estimate_cost")
async def estimate_cost(
    *,
    db: AsyncSession,
    namespace_id: int,
    db_type: str,
    database: str,
    target: str,
    query: dict,
) -> dict:
    """预估查询扫描行数与风险等级."""
    ds = await resolve_ds(db, namespace_id, db_type, database)
    if ds is None:
        return _ds_not_found_error(namespace_id, db_type, database)

    try:
        driver = get_driver(db_type)
        cost = await driver.estimate_cost(ds, target, query)
        return await _attach_server_capabilities(dict(cost), driver, ds)
    except DriverError as e:
        return _error_from_driver(e)


# ════════════════════════════════════════════
#  execute_query — 执行查询 (4 mode)
# ════════════════════════════════════════════

@observe(name="tool.execute_query")
async def execute_query(
    *,
    db: AsyncSession,
    namespace_id: int,
    db_type: str,
    database: str,
    target: str,
    query: dict,
    mode: str = "single",
    batch_size: int = 1000,  # noqa: hardcode
) -> dict:
    """执行查询, 按 mode 控制粒度 (single/probe/count/batched)."""
    ds = await resolve_ds(db, namespace_id, db_type, database)
    if ds is None:
        return _ds_not_found_error(namespace_id, db_type, database)

    try:
        driver = get_driver(db_type)
        result = await driver.execute_query(ds, target, query, mode=mode, batch_size=batch_size)  # type: ignore[arg-type]
        return dict(result)
    except DriverError as e:
        return _error_from_driver(e)
