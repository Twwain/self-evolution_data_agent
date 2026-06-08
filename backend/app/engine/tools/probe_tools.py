"""Stage 4 Task 4 — inspect_field_values tool (read-only Mongo sampling).

agent 拿真实样本值判断字段形态 (ObjectId vs string vs enum), 不靠瞎猜.
"""
from __future__ import annotations

import logging

from langfuse import observe

from app.config import settings

from ._mongo_helpers import close_db, get_mongo_db, record_span_io

log = logging.getLogger(__name__)


# ════════════════════════════════════════════
#  读路径 — Motor read-only 采样, 无写副作用
# ════════════════════════════════════════════

@observe(name="tool.inspect_field_values")
async def inspect_field_values(
    *, namespace_id: int, collection: str, field: str, database: str,
    sample: int | None = None,
) -> dict:
    """真查 mongo 取字段样本值, read-only, 帮 LLM 判断字段形态.

    默认 sample = settings.inspect_field_default_sample (5).
    只取 {field: {$exists: True}} 的文档, 投影 {field: 1, _id: 0} 减带宽.
    """
    if sample is None:
        sample_n = settings.inspect_field_default_sample
    else:
        sample_n = sample
    if sample_n <= 0:
        return {
            "collection": collection, "field": field,
            "values": [], "truncated": False, "sample_requested": sample_n,
        }
    db_ = await get_mongo_db(namespace_id=namespace_id, database=database)
    try:
        actual_field = field
        note: str | None = None

        values = await _sample_field(db_, collection, field, sample_n)

        # 方案 B: 原字段 0 结果 + field=="id" → fallback 查 _id
        if not values and field == "id":
            values = await _sample_field(db_, collection, "_id", sample_n)
            if values:
                actual_field = "_id"
                note = (
                    f"字段 '{field}' 在文档中不存在, "
                    f"已 fallback 到 MongoDB 主键 '_id'"
                )

        out = {
            "collection": collection, "field": field,
            "values": values, "truncated": len(values) >= sample_n,
            "sample_requested": sample_n,
        }
        if note:
            out["note"] = note
        record_span_io(
            input={"namespace_id": namespace_id, "collection": collection,
                    "field": field, "database": database, "sample": sample_n},
            output={"value_count": len(values), "truncated": out["truncated"],
                    "actual_field": actual_field},
        )
        return out
    finally:
        close_db(db_)


async def _sample_field(db_, collection: str, field: str, n: int) -> list:
    """从 collection 采样 n 条含 field 的文档, 返回字段值列表.

    _id 字段特殊处理: MongoDB 投影中 {_id: 1, _id: 0} 自相矛盾,
    需单独用 {_id: 1} (不排除自身).
    """
    if field == "_id":
        projection = {"_id": 1}
    else:
        projection = {field: 1, "_id": 0}

    cursor = db_[collection].find({field: {"$exists": True}}, projection)
    cursor = cursor.limit(n)
    return [doc.get(field) async for doc in cursor]
