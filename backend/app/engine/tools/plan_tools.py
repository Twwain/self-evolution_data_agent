"""Stage 4 Task 8 — plan_tools wrappers (generate / execute / chart).

Thin wrappers around plan_generator / plan_executor / visualizer for agent loop.
Agent passes targets/conditions/schemas as dicts; we rebuild Target/Condition/QueryPlan
dataclasses internally — never make agent LLM construct dataclasses.

Stage 4 Task 12 完成: dataclass 已迁出至 app.engine.plan_models, decomposer 全栈已删.
本模块是 agent loop 重��水合 Target/Condition/QueryPlan 的唯一合法入口.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import pandas as pd
from langfuse import observe
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.drivers import get_driver
from app.engine.plan_executor import execute_plan
from app.engine.plan_generator import generate_plan
from app.engine.plan_models import (
    PlanStep,
    QueryPlan,
)
from app.engine.tools._resolve_ds import resolve_ds
from app.engine.visualizer import recommend_chart

from ._mongo_helpers import record_span_io

log = logging.getLogger(__name__)


# ════════════════════════════════════════════
#  dict → dataclass 重建器
# ════════════════════════════════════════════

def _dict_to_plan_step(d: dict) -> PlanStep:
    return PlanStep(
        step_idx=d.get("step_idx", 0),
        database=d.get("database", ""),
        collection=d.get("collection", ""),
        operation=d.get("operation", "aggregate"),
        pipeline=list(d.get("pipeline") or []),
        query=dict(d.get("query") or {}),
        projection=dict(d.get("projection") or {}),
        sort=list(d.get("sort") or []),
        limit=d.get("limit", 1000),
        exports=list(d.get("exports") or []),
        db_type=d.get("db_type", "mongodb"),
    )


def _dict_to_query_plan(plan_dict: dict) -> QueryPlan:
    """从 dict 重建 QueryPlan (无 from_dict, 手工拼). 保持与 to_dict 对称."""
    return QueryPlan(
        strategy=plan_dict.get("strategy", "single_aggregate"),
        steps=[_dict_to_plan_step(s) for s in plan_dict.get("steps") or []],
        post_process=plan_dict.get("post_process", ""),
    )


# ════════════════════════════════════════════
#  Tool 1: generate_query_plan
# ════════════════════════════════════════════

@observe(name="tool.generate_query_plan")
async def generate_query_plan(
    *, db: AsyncSession, question: str, namespace_id: int,
    collections: list[dict], schemas: dict,
    filters: list[dict] | None = None,
    knowledge: list[str] | None = None,
    rules: list[str] | None = None,
) -> dict:
    """生成跨引擎多步查询 Plan. agent 友好接口 (dict in / dict out).

    collections 含每个 (db_type, database, collection); plan steps 据此按 db_type
    多态生成 (mysql=sql / mongodb=pipeline). plan_executor 按 (ns, db_type, database)
    反查 ds 执行.

    db / namespace_id 是 dispatcher 按签名注入的 runtime context (LLM 不可见, 不在
    TOOL_SPECS); 用于内部按集合反查 datasource 并解析 server_capabilities.
    """
    # Resolve per-collection capabilities INTERNALLY (LLM never passes these).
    capabilities_by_target = await _resolve_caps_by_target(db, namespace_id, collections)
    plan = await generate_plan(
        question, collections, filters or [], schemas, knowledge, rules,
        capabilities_by_target=capabilities_by_target,
    )
    plan_dict = plan.to_dict()
    out: dict[str, Any] = {"plan": plan_dict}
    record_span_io(
        input={
            "question": question[:200],
            "collection_count": len(collections),
            "filter_count": len(filters or []),
            "schema_count": len(schemas),
            "caps_targets": sorted(capabilities_by_target.keys()),  # observability only
        },
        output={
            "plan_strategy": plan_dict.get("strategy"),
            "step_count": len(plan_dict.get("steps") or []),
        },
    )
    return out


# ────────────────────────────────────────────
#  内部: per-collection capability 解析 (LLM 不可见)
#  镜像 data_access_tools._attach_server_capabilities 的解析路径:
#  resolve_ds → get_driver(db_type).get_server_capabilities(ds), 失败安全.
# ────────────────────────────────────────────

def _caps_target_key(db_type: str, database: str, collection: str) -> str:
    """Stable key joining a collection to its resolved capabilities.
    Must match the key used by _format_collections and the executor."""
    return f"[{db_type}] {database}.{collection}"


def _has_restrictions(caps: Mapping[str, Any]) -> bool:
    """True if caps declares any of the three restriction categories."""
    return bool(
        caps.get("unsupported_ops")
        or caps.get("unsupported_stage_variants")
        or caps.get("syntax_constraints")
    )


async def _resolve_caps_by_target(
    db: AsyncSession, namespace_id: int, collections: list[dict],
) -> dict[str, dict]:
    """Per-collection capability resolution at the DATASOURCE dimension.

    For each mongodb collection: resolve_ds → get_server_capabilities (per-ds cached,
    failure-safe). Native/empty-restriction caps are omitted (no noise). mysql
    collections are skipped (no mongo capability concept).
    """
    out: dict[str, dict] = {}
    for c in collections:
        db_type = (c.get("db_type") or "mongodb").strip()
        if db_type != "mongodb":
            continue
        database = (c.get("database") or "").strip()
        collection = (c.get("collection") or "").strip()
        if not database or not collection:
            continue
        ds = await resolve_ds(db, namespace_id, db_type, database)
        if ds is None:
            continue
        try:
            caps = await get_driver(db_type).get_server_capabilities(ds)
        except Exception:  # noqa: BLE001 — capability probe must never block planning
            caps = None
        if caps is not None and _has_restrictions(caps):
            out[_caps_target_key(db_type, database, collection)] = dict(caps)
    return out


# ════════════════════════════════════════════
#  Tool 2: execute_plan_tool
#  (名称避让 plan_executor.execute_plan)
# ════════════════════════════════════════════

@observe(name="tool.execute_plan")
async def execute_plan_tool(
    *, namespace_id: int, ns_slug: str, plan: dict,
    sse_emit=None,
) -> dict:
    """串行执行 multi-step Plan, 返回最终行 + 列名 + 步骤 trace."""
    qp = _dict_to_query_plan(plan)
    result = await execute_plan(qp, slug=ns_slug, ns_id=namespace_id, sse_emit=sse_emit)

    rows: list[dict] = list(getattr(result, "final", []) or [])
    columns: list[str] = []
    if rows and isinstance(rows[0], dict):
        # 用首行 key 顺序作为 columns; dict 默认 insertion order (3.7+ 稳定)
        columns = list(rows[0].keys())

    out = {
        "rows": rows,
        "columns": columns,
        "last_step_idx": getattr(result, "last_step_idx", 0),
    }
    record_span_io(
        input={
            "namespace_id": namespace_id,
            "ns_slug": ns_slug,
            "plan_steps": len(plan.get("steps") or []),
        },
        output={
            "row_count": len(rows),
            "column_count": len(columns),
            "last_step_idx": out["last_step_idx"],
        },
    )
    return out


# ════════════════════════════════════════════
#  Tool 3: recommend_chart_tool
#  (sync, recommend_chart 本身就是 sync)
# ════════════════════════════════════════════

@observe(name="tool.recommend_chart")
def recommend_chart_tool(*, rows: list[dict], columns: list[str], category_column: str = "") -> dict:
    """启发式图表推荐. 空结果落 table, 不进 visualizer."""
    if not rows:
        return {"chart_type": "table", "config": {}, "category_column": ""}
    # 校验 columns 是否与 rows keys 匹配，不匹配则 fallback 到 dict keys
    actual_keys = set(rows[0].keys())
    # 确保 category_column 在 DataFrame 中（LLM 可能没把它放进 columns）
    use_columns: list[str] | None = None
    if columns and set(columns) <= actual_keys:
        use_columns = list(columns)
        if category_column and category_column in actual_keys and category_column not in use_columns:
            use_columns.append(category_column)
    if use_columns:
        df = pd.DataFrame(rows, columns=use_columns)
    else:
        df = pd.DataFrame(rows)
    # 校验 category_column 有效性
    if category_column and category_column not in actual_keys:
        category_column = ""
    chart_type, config = recommend_chart(df, category_column=category_column)
    out = {"chart_type": chart_type, "config": config, "category_column": category_column}
    record_span_io(
        input={"row_count": len(rows), "column_count": len(use_columns or list(actual_keys)), "category_column": category_column},
        output={"chart_type": chart_type},
    )
    return out
