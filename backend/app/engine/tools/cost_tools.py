"""Stage 4 Task 6 — cost-aware tools (estimate / count_only / batched_aggregate).

三件套帮 agent 决策"这查询能不能跑、要不要切换 count、需不需要分批":

- estimate_query_cost: read-only explain('executionStats') 估扫描行数 + 命中索引
- execute_count_only:  极便宜 count_documents / distinct, 不拉数据
- execute_batched_aggregate: 按 batch_field 切 batch_ids 跑 aggregate, 大循环必带
                              cancel 检查点 (asyncio.sleep(0))

事务契约: 全部 read-only, 不 commit, 不动 SQLite, 仅 mongo 直查.
"""
from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any

from langfuse import observe

from app.config import settings

from ._mongo_helpers import close_db, get_mongo_db, record_span_io

log = logging.getLogger(__name__)


# ════════════════════════════════════════════
#  estimate_query_cost — explain 估算
# ════════════════════════════════════════════

# explain 输出剪枝白名单 — LLM 用不到的元信息 / 长尾 plan 历史
_EXPLAIN_DROP_KEYS = frozenset({
    "$clusterTime", "operationTime", "serverInfo", "command",
    "rejectedPlans", "allPlansExecution",
})
_EXPLAIN_MAX_ARRAY_ITEMS = 20


def _prune_explain(node: Any) -> Any:
    """递归剪除元信息 + 截断长数组, 控制 explain 输出体积喂给 LLM."""
    if isinstance(node, dict):
        return {
            k: _prune_explain(v)
            for k, v in node.items()
            if k not in _EXPLAIN_DROP_KEYS
        }
    if isinstance(node, list):
        if len(node) > _EXPLAIN_MAX_ARRAY_ITEMS:
            return [_prune_explain(v) for v in node[:_EXPLAIN_MAX_ARRAY_ITEMS]] + [
                {"_truncated": len(node) - _EXPLAIN_MAX_ARRAY_ITEMS}
            ]
        return [_prune_explain(v) for v in node]
    return node


@observe(name="tool.estimate_query_cost")
async def estimate_query_cost(
    *, namespace_id: int, collection: str, filter: dict, database: str,
    sse_emit,
    pipeline_stages: list | None = None,
) -> dict:
    """走 explain 估代价, 完全 read-only — 双路径分发.

    - pipeline_stages 为空 / None → find filter 路径, 返结构化
      `{estimated_docs, hit_indexes, warning}`
    - pipeline_stages 非空        → aggregate explain 路径, 返
      `{mongo_version, explain_raw, hint}` 由 LLM 自决 (跨 mongo 版本零代码维护)
    """
    db_ = await get_mongo_db(namespace_id=namespace_id, database=database)
    try:
        if pipeline_stages:
            return await _estimate_aggregate(db_, collection, filter, pipeline_stages)
        result = await _estimate_find(db_, collection, filter)
        # ── P0-3 emit cost_warning — 超阈时推送 (find 路径专属) ──
        if (
            result.get("warning")
            and result.get("estimated_docs", 0) > settings.query_cost_single_layer_limit
        ):
            await sse_emit({"event": "cost_warning", "data": {
                "estimated_docs": result["estimated_docs"],
                "threshold": settings.query_cost_single_layer_limit,
                "advice": "考虑使用 execute_count_only 短路或 execute_batched_aggregate 分批降级",
            }})
        return result
    finally:
        close_db(db_)


async def _estimate_find(db_, collection: str, filter: dict) -> dict:
    """find filter 路径 — 结构化输出, 与历史调用方契约不变.

    DocumentDB 兼容: explain() 不返回 executionStats (仅 queryPlanner),
    此时 fallback 到 count_documents 获取真实匹配数.
    """
    explain = await db_[collection].find(filter).explain()
    stats = explain.get("executionStats", {}) if isinstance(explain, dict) else {}
    planner = explain.get("queryPlanner", {}) if isinstance(explain, dict) else {}
    hit_indexes = _collect_indexes(planner.get("winningPlan", {}))

    if stats:
        # 原生 MongoDB: executionStats 可用
        est = stats.get("totalDocsExamined", stats.get("nReturned", 0))
    else:
        # DocumentDB: executionStats 缺失, fallback count_documents
        est = await db_[collection].count_documents(filter)

    warning: str | None = None
    if est > settings.query_cost_single_layer_limit:
        warning = (
            f"single_layer_overflow (>{settings.query_cost_single_layer_limit:,})"
        )

    out = {"estimated_docs": est, "hit_indexes": hit_indexes, "warning": warning}
    record_span_io(
        input={"path": "find", "collection": collection, "filter_keys": list(filter.keys())},
        output={
            "estimated_docs": est,
            "index_count": len(hit_indexes),
            "has_warning": warning is not None,
            "fallback_count": not bool(stats),
        },
    )
    return out


async def _estimate_aggregate(
    db_, collection: str, filter: dict, pipeline_stages: list,
) -> dict:
    """aggregate explain 路径 — pruned raw + mongo_version 喂 LLM 自决."""
    server_info = await db_.client.server_info()
    mongo_version = server_info.get("version", "unknown")

    full_pipeline = ([{"$match": filter}] if filter else []) + pipeline_stages
    explain = await db_.command({
        "explain": {
            "aggregate": collection,
            "pipeline": full_pipeline,
            "cursor": {},
        },
        "verbosity": "executionStats",
    })
    pruned = _prune_explain(explain)

    out = {
        "mongo_version": mongo_version,
        "explain_raw": pruned,
        "hint": (
            "Read explain_raw to find: totalDocsExamined per stage, COLLSCAN signals, "
            "indexName usage, SORT without index, $lookup amplification. Decide whether "
            "to abort, narrow filter, or fall back to execute_count_only / "
            "execute_batched_aggregate. mongo_version tells you which explain shape to expect."
        ),
    }
    record_span_io(
        input={
            "path": "aggregate",
            "collection": collection,
            "mongo_version": mongo_version,
            "stage_count": len(pipeline_stages),
        },
        output={"explain_bytes": len(str(pruned))},
    )
    return out


def _collect_indexes(plan: Any) -> list[str]:
    """DFS 抽 winningPlan 各级 indexName (winningPlan 嵌套不规则, 必须递归)."""
    names: list[str] = []
    if isinstance(plan, dict):
        idx = plan.get("indexName")
        if idx:
            names.append(idx)
        for v in plan.values():
            names.extend(_collect_indexes(v))
    elif isinstance(plan, list):
        for v in plan:
            names.extend(_collect_indexes(v))
    return names


# ════════════════════════════════════════════
#  execute_count_only — 不拉数据, 只数数
# ════════════════════════════════════════════

@observe(name="tool.execute_count_only")
async def execute_count_only(
    *, namespace_id: int, collection: str, filter: dict, database: str,
    distinct_field: str | None = None,
) -> dict:
    """count_documents (+ optional distinct), 极便宜, 不拉数据."""
    db_ = await get_mongo_db(namespace_id=namespace_id, database=database)
    try:
        count = await db_[collection].count_documents(filter)
        distinct_count: int | None = None
        if distinct_field:
            vals = await db_[collection].distinct(distinct_field, filter)
            distinct_count = len(vals)
    finally:
        close_db(db_)

    out = {"count": count, "distinct_count": distinct_count}
    record_span_io(
        input={
            "namespace_id": namespace_id,
            "collection": collection,
            "database": database,
            "distinct_field": distinct_field,
        },
        output=out,
    )
    return out


# ════════════════════════════════════════════
#  execute_batched_aggregate — 大列表切批
# ════════════════════════════════════════════

@observe(name="tool.execute_batched_aggregate")
async def execute_batched_aggregate(
    *, namespace_id: int, collection: str, database: str,
    pipeline_template: list[dict], batch_field: str,
    batch_ids: list[Any], batch_size: int | None = None,
) -> dict:
    """按 batch_size 切 batch_ids, 字面 '<batch>' 占位运行时替换为当前 chunk.

    每 batch 头部 await asyncio.sleep(0) — cancel 检查点 (大循环关键,
    用户 POST /cancel 后 worker 必须能 yield 回事件循环)
    """
    if batch_size is None:
        batch_size = settings.query_cost_default_batch_size

    db_ = await get_mongo_db(namespace_id=namespace_id, database=database)
    all_results: list[dict] = []
    batch_sizes: list[int] = []
    try:
        coll = db_[collection]
        for offset in range(0, len(batch_ids), batch_size):
            await asyncio.sleep(0)  # cancel 检查点
            chunk = batch_ids[offset:offset + batch_size]
            batch_sizes.append(len(chunk))
            pipeline = [_substitute_batch(stage, chunk) for stage in pipeline_template]
            async for row in coll.aggregate(pipeline, allowDiskUse=True):
                all_results.append(row)
    finally:
        close_db(db_)

    out = {
        "total_batches": len(batch_sizes),
        "batch_sizes": batch_sizes,
        "rows": all_results,
        "row_count": len(all_results),
    }
    record_span_io(
        input={
            "namespace_id": namespace_id,
            "collection": collection,
            "database": database,
            "batch_field": batch_field,
            "total_ids": len(batch_ids),
            "batch_size": batch_size,
        },
        output={
            "total_batches": len(batch_sizes),
            "row_count": len(all_results),
        },
    )
    return out


def _substitute_batch(stage: dict, chunk: list) -> dict:
    """深拷贝 stage 后 walk dict/list, 把字面 '<batch>' 替换为 chunk 列表.

    list 分支必须支持元素位替换: 形如 `{"$expr": {"$in": ["$_id", "<batch>"]}}` 的
    常见 mongo 模式中 <batch> 直接作为 list 元素 (而非 dict value), 早期实现只递归
    不替换会导致 silently 漏替换, agent 拿到原样 "<batch>" 字符串去查 mongo 必抛错.
    """
    s = copy.deepcopy(stage)

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in list(node.items()):
                if v == "<batch>":
                    node[k] = chunk
                else:
                    _walk(v)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                if v == "<batch>":
                    node[i] = chunk
                else:
                    _walk(v)

    _walk(s)
    return s
