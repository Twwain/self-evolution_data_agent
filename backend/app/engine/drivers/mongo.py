"""MongoDB 异步驱动 — motor AsyncIOMotorClient + aggregation 执行."""
from __future__ import annotations

import logging
import time
from typing import Any

from bson import DBRef, ObjectId
from bson.decimal128 import Decimal128
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from app.engine.drivers._exceptions import (
    ConnectionFailureError,
    PayloadShapeMismatchError,
)
from app.engine.drivers.base import (
    CostEstimate,
    ExecuteMode,
    ExecuteResult,
    FieldDef,
    SchemaSnapshot,
    ServerCapabilities,
)
from app.models import DataSource

log = logging.getLogger(__name__)


def _infer_bson_type(value: object) -> str:
    """从 sample 值推断 BSON 类型名."""
    if value is None:
        return "null"
    type_map = {
        "str": "string",
        "int": "int",
        "float": "double",
        "bool": "bool",
        "list": "array",
        "dict": "object",
    }
    name = type(value).__name__
    return type_map.get(name, name)


_BSON_MAX_DEPTH = 64  # noqa: hardcode — 深度护栏, 防病态嵌套递归爆栈; 真实文档远小于此


def _normalize_doc(doc: dict) -> dict:
    """行级规整: doc 顶层恒为 dict, 包一层确保类型为 dict (供 list[dict] append)."""
    result = _normalize_bson(doc)
    return result if isinstance(result, dict) else {"_value": result}


def _normalize_bson(value: object, _depth: int = 0) -> object:
    """递归把驱动返回的 BSON 特殊类型转成 LLM 友好结构.

    解决 DBRef 序列化退化为不可解析字符串 (如 "DBRef('c_x', ObjectId('...'))") 的问题:
    LLM 抠不出内嵌 id, 被迫反复试错。统一在执行后、回传前规整一次。

    - DBRef     → {"$ref": <collection>, "$id_str": <id 字符串>, "$id_type": <id 类型名>}
                  ($id_str 是裸字符串, LLM 直接拿去匹配目标集合的关联字段)
    - ObjectId  → str()  (不止顶层 _id, 任意嵌套位置)
    - Decimal128 → str()
    - bytes     → repr() (二进制不直喂 LLM)
    - dict/list → 递归
    - 其它原生 JSON 类型原样

    性能: 实测 ~0.03ms/行 (含 8 元素 DBRef 数组的重嵌套文档), 1000 行上限 ~31ms,
    远小于单次 Mongo 查询 (30-380ms) 与 LLM 轮次 (6-13s)。两处廉价护栏:
    (1) 基础标量 fast-path 前置, 跳过全部 isinstance 特判;
    (2) _depth 上限防病态嵌套爆栈 (超限降级 str(), 不抛异常阻断主路径)。
    """
    # fast-path: 绝大多数值是基础标量, 直接返回, 避开后续所有 isinstance 检查
    if value is None or type(value) in (str, int, float, bool):
        return value
    if _depth >= _BSON_MAX_DEPTH:
        return str(value)
    if isinstance(value, DBRef):
        return {
            "$ref": value.collection,
            "$id_str": str(value.id),
            "$id_type": type(value.id).__name__,
        }
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, Decimal128):
        return str(value)
    if isinstance(value, bytes):
        return repr(value)
    if isinstance(value, dict):
        return {k: _normalize_bson(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_bson(v, _depth + 1) for v in value]
    return value


def _classify_query_shape(query: dict) -> tuple[str, Any]:
    """聚合 vs 过滤的形态判定唯一来源 (scope C).

    query 携带 pipeline → ("aggregate", pipeline); 否则 → ("filter", filter_dict)。
    count / 数据读取 (single/probe/batched) / estimate_cost 三处共用, 杜绝形态判定
    在不同模式间漂移 (历史 bug: count 丢 pipeline, estimate_cost 用 truthiness)。

    用 `is not None` (而非 truthiness): 显式空 pipeline [] 归为 aggregate —— 与历史
    数据读取路径行为一致, 并纠正 estimate_cost 的 truthiness 误分类。
    """
    pipeline = query.get("pipeline")
    if pipeline is not None:
        return "aggregate", pipeline
    return "filter", query.get("filter", {})


class MongoDriver:
    """motor AsyncIOMotorClient 驱动, 实现 DataSourceDriver 协议."""

    db_type: str = "mongodb"

    def __init__(self) -> None:
        self._clients: dict[int, AsyncIOMotorClient] = {}
        self._caps_cache: dict[int, ServerCapabilities] = {}

    def _get_client(self, ds: DataSource) -> AsyncIOMotorClient:
        """获取或创建 ds 对应的 motor client."""
        if ds.id in self._clients:
            return self._clients[ds.id]
        try:
            uri = f"mongodb://{ds.username}:{ds.password}@{ds.host}:{ds.port}/{ds.database}"
            client = AsyncIOMotorClient(
                uri,
                maxPoolSize=settings.mongo_pool_max_size,
                authSource=ds.database,
            )
        except Exception as exc:
            raise ConnectionFailureError(
                f"MongoDB 连接失败: {ds.host}:{ds.port}/{ds.database} — {exc}",
                suggestion="检查 host/port/credentials 是否正确",
            ) from exc
        self._clients[ds.id] = client
        return client

    # ── fetch_schema ─────────────────────────────────────

    async def fetch_schema(
        self,
        ds: DataSource,
        target: str | None = None,
    ) -> SchemaSnapshot | list[SchemaSnapshot]:
        log.info("[mongo_driver] fetch_schema ds=%d target=%s", ds.id, target)
        client = self._get_client(ds)
        db = client[ds.database]

        if target is None:
            # 列出所有集合
            names = await db.list_collection_names()
            results: list[SchemaSnapshot] = []
            for name in names:
                results.append(
                    SchemaSnapshot(
                        db_type="mongodb",
                        database=ds.database,
                        target=name,
                        description="",
                        fields=[],
                        indexes=[],
                        sample_count=0,
                    )
                )
            return results

        # 单集合详情
        coll = db[target]

        # 采样推断字段
        sample = await coll.find_one()
        fields: list[FieldDef] = []
        if sample:
            for key, value in sample.items():
                if key == "_id":
                    continue
                fields.append(
                    FieldDef(
                        name=key,
                        type=_infer_bson_type(value),
                        description="",
                        indexed=False,
                        nullable=True,
                    )
                )

        # 索引信息
        indexes: list[dict] = []
        async for idx_info in coll.list_indexes():
            indexes.append({
                "name": idx_info.get("name", ""),
                "keys": idx_info.get("key", {}),
                "unique": idx_info.get("unique", False),
            })

        # 标记 indexed 字段
        indexed_fields: set[str] = set()
        for idx in indexes:
            for k in idx.get("keys", {}):
                indexed_fields.add(k)
        for f in fields:
            if f["name"] in indexed_fields:
                f["indexed"] = True

        # 文档数估算
        sample_count = await coll.estimated_document_count()

        return SchemaSnapshot(
            db_type="mongodb",
            database=ds.database,
            target=target,
            description="",
            fields=fields,
            indexes=indexes,
            sample_count=sample_count,
        )

    # ── inspect_values ───────────────────────────────────

    async def inspect_values(
        self,
        ds: DataSource,
        target: str,
        field: str,
        limit: int = 10,
    ) -> list[dict]:
        log.info("[mongo_driver] inspect_values ds=%d target=%s field=%s", ds.id, target, field)
        client = self._get_client(ds)
        db = client[ds.database]
        coll = db[target]

        pipeline = [
            {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": limit},
        ]
        results: list[dict] = []
        async for doc in coll.aggregate(pipeline):
            results.append({"value": _normalize_bson(doc["_id"]), "count": doc["count"]})
        return results

    # ── estimate_cost ────────────────────────────────────

    async def estimate_cost(
        self,
        ds: DataSource,
        target: str,
        query: dict,
    ) -> CostEstimate:
        log.info("[mongo_driver] estimate_cost ds=%d target=%s", ds.id, target)
        client = self._get_client(ds)
        db = client[ds.database]
        coll = db[target]

        shape, payload = _classify_query_shape(query)
        if shape == "aggregate":
            explain_result = await db.command(
                "explain",
                {"aggregate": target, "pipeline": payload, "cursor": {}},
                verbosity="queryPlanner",
            )
        else:
            explain_result = await db.command(
                "explain",
                {"find": target, "filter": payload},
                verbosity="queryPlanner",
            )

        # 估算行数 — 使用 estimated_document_count 作为上界
        estimated_rows = await coll.estimated_document_count()
        if estimated_rows > settings.query_cost_total_limit:
            level = "blocked"
        elif estimated_rows > settings.query_cost_single_layer_limit:
            level = "high"
        else:
            level = "ok"

        return CostEstimate(
            estimated_rows=estimated_rows,
            warning_level=level,
            raw_explain=explain_result,
        )

    # ── execute_query ────────────────────────────────────

    async def execute_query(
        self,
        ds: DataSource,
        target: str,
        query: dict,
        mode: ExecuteMode = "single",
        batch_size: int = 1000,  # noqa: hardcode
    ) -> ExecuteResult:
        log.info("[mongo_driver] execute_query ds=%d target=%s mode=%s", ds.id, target, mode)

        # payload 校验
        if "sql" in query:
            raise PayloadShapeMismatchError(
                "MongoDB driver 不接受 'sql' key",
                suggestion="使用 'pipeline' 或 'filter' key",
            )
        if "pipeline" not in query and "filter" not in query:
            raise PayloadShapeMismatchError(
                "execute_query 需要 'pipeline' 或 'filter' key",
                suggestion="payload 必须包含 'pipeline' (聚合) 或 'filter' (查询)",
            )

        client = self._get_client(ds)
        db = client[ds.database]
        coll = db[target]

        t0 = time.perf_counter()

        # count 模式
        if mode == "count":
            # 形态判定走共享 classifier (与数据读取 / estimate_cost 同源, 不再漂移)。
            # 契约 (R5): count pipeline 必须仅含过滤型 stage ($match 等); 驱动追加
            # {"$count":"count"}, 不剥离末尾 $limit/$skip/$sample —— 行截断 stage 会
            # 让 $count 统计被截断的集合, 这是调用方错误, 应暴露而非静默掩盖。
            shape, payload = _classify_query_shape(query)
            if shape == "aggregate":
                # 经 aggregate + $count 计数。标准 MongoDB & DocumentDB 兼容, 无 flavor
                # 分支。0 匹配文档 → aggregate 产 0 行 → count 0。
                count_pipeline = list(payload)  # 不修改调用方 pipeline
                count_pipeline.append({"$count": "count"})
                count = 0
                async for doc in coll.aggregate(count_pipeline):
                    count = int(doc.get("count", 0))
                    break
            else:
                count = await coll.count_documents(payload)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            log.info(
                "[mongo_driver] execute_query done ds=%d count=%d elapsed_ms=%d",
                ds.id,
                count,
                elapsed_ms,
            )
            return ExecuteResult(
                rows=[{"count": count}],
                row_count=1,
                truncated=False,
                elapsed_ms=elapsed_ms,
            )

        # 确定 limit
        if mode == "probe":
            limit = 10
        elif mode == "batched":
            limit = batch_size
        else:
            limit = settings.query_row_limit

        shape, payload = _classify_query_shape(query)
        if shape == "aggregate":
            # aggregate 模式 — 追加 $limit
            pipeline = list(payload)  # 不修改原始
            pipeline.append({"$limit": limit})
            rows: list[dict] = []
            async for doc in coll.aggregate(pipeline):
                # 规整 BSON (DBRef → {$ref,$id_str} / 嵌套 ObjectId → str / ...)
                rows.append(_normalize_doc(doc))
        else:
            # find 模式
            cursor = coll.find(payload).limit(limit)
            rows = []
            async for doc in cursor:
                rows.append(_normalize_doc(doc))

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        truncated = len(rows) >= limit

        log.info(
            "[mongo_driver] execute_query done ds=%d rows=%d elapsed_ms=%d",
            ds.id,
            len(rows),
            elapsed_ms,
        )
        return ExecuteResult(
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=elapsed_ms,
        )

    # ── health_check ─────────────────────────────────────

    async def health_check(self, ds: DataSource) -> bool:
        try:
            client = self._get_client(ds)
            await client.admin.command("ping")
            return True
        except Exception:
            return False

    # ── get_server_capabilities ──────────────────────────

    async def get_server_capabilities(
        self, ds: DataSource,
    ) -> ServerCapabilities | None:
        """Return server version + flavor-aware capability restrictions.

        Cached per-ds (driver lifetime). buildInfo failure → None
        (never block primary read/write paths, 不缓存 None).
        flavor 探测 + 三类能力计算下沉到 mongo_flavor.build_capabilities (失败安全)。
        """
        cached = self._caps_cache.get(ds.id)
        if cached is not None:
            return cached
        try:
            client = self._get_client(ds)
            info = await client.admin.command("buildInfo")
        except Exception as exc:  # noqa: BLE001 — buildInfo 失败不阻业务
            log.warning("[mongo_driver] buildInfo failed ds=%d: %s", ds.id, exc)
            return None  # 不缓存 None (R3.6)
        version = info.get("version", "")
        if not version:
            return None  # 无版本 → 不缓存 (与现状一致)
        # flavor 探测 + 能力计算 (失败安全, 内部回退原生)
        from app.engine.drivers.mongo_flavor import build_capabilities
        caps = build_capabilities(dict(info), version)
        self._caps_cache[ds.id] = caps  # 仅缓存成功结果 (R3.2)
        return caps

    # ── lifecycle ────────────────────────────────────────

    async def close_all(self) -> None:
        """关闭所有 motor client."""
        for client in self._clients.values():
            client.close()
        self._clients.clear()
