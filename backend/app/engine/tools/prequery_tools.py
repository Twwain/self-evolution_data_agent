"""Stage 4 Task 5 — prequery 工具 (Motor 直查 mongo, 不依赖 decomposer 数据结构).

为什么不复用 app/engine/prequery.py:run_prequery — 它耦合 DecomposerOutput,
且 Stage 4 Task 12 会删 decomposer. agent loop 需要的是简化版.

Returns:
    prequery_collection → {candidates, total, status}
    prequery_with_field_extraction →
        {candidates, total, status, upstream_count, upstream_ids}
"""
from __future__ import annotations

import logging

from langfuse import observe

from app.config import settings

from ._mongo_helpers import (
    _LABEL_FIELDS,
    close_db,
    extract_candidate,
    get_mongo_db,
    record_span_io,
)

log = logging.getLogger(__name__)


# ════════════════════════════════════════════
#  Tool 1: prequery_collection
# ════════════════════════════════════════════

@observe(name="tool.prequery_collection")
async def prequery_collection(
    *, namespace_id: int, collection: str,
    fields: list[str], pattern: str, database: str,
    extra_keep_fields: tuple[str, ...] = (),
) -> dict:
    """按 fields 中任一字段 regex 匹配 pattern, 返候选 ID 列表.

    状态机:
      - ok: 0 < 命中 ≤ overflow_threshold
      - overflow: 命中 > overflow_threshold (candidates 截断到 threshold)
      - zero_hit: 0 命中

    fields=[] 或 pattern="" 时直接返 zero_hit (不发请求).

    extra_keep_fields: 透传给 extract_candidate, 让 meta 保留指定 label-class
    字段 (BL-05 链路 extract_field=name 时必须 keep ("name",)).
    """
    threshold = settings.prequery_overflow_threshold
    if not fields or not pattern:
        out = {"candidates": [], "total": 0, "status": "zero_hit"}
        record_span_io(
            input={"namespace_id": namespace_id, "collection": collection,
                    "database": database,
                    "fields": fields, "pattern": pattern},
            output={"status": "zero_hit", "total": 0,
                     "reason": "empty_fields_or_pattern"},
        )
        return out

    db_ = await get_mongo_db(namespace_id=namespace_id, database=database)
    try:
        # M1: case-insensitive regex (避免大小写敏感漏命中)
        query = {"$or": [
            {f: {"$regex": pattern, "$options": "i"}} for f in fields
        ]}
        cursor = db_[collection].find(query).limit(threshold + 1)
        docs = [d async for d in cursor]
    finally:
        close_db(db_)

    overflow = len(docs) > threshold
    kept = docs[:threshold]
    candidates = [
        extract_candidate(d, extra_keep_fields=extra_keep_fields) for d in kept
    ]
    status = "overflow" if overflow else ("zero_hit" if not kept else "ok")
    out = {"candidates": candidates, "total": len(docs), "status": status}
    record_span_io(
        input={"namespace_id": namespace_id, "collection": collection,
                "database": database,
                "fields": fields, "pattern": pattern},
        output={"status": status, "total": len(docs),
                 "candidate_count": len(candidates)},
    )
    return out


# ════════════════════════════════════════════
#  Tool 2: prequery_with_field_extraction (BL-05)
# ════════════════════════════════════════════

@observe(name="tool.prequery_with_field_extraction")
async def prequery_with_field_extraction(
    *, namespace_id: int,
    upstream_coll: str, extract_field: str,
    upstream_fields: list[str], upstream_pattern: str,
    downstream_coll: str, downstream_filter_field: str,
    database: str,
) -> dict:
    """治 BL-05 — 上游 regex 命中后 extract 字段, 作为下游过滤键.

    例: c_category.name=~"优选" → categoryIds → c_product.categoryId∈categoryIds → 候选订单.
    上游 0 命中 → 短路返 zero_hit_upstream, 不查下游.

    修 review I2: 当 extract_field 落在 _LABEL_FIELDS 中时 (如 name/title), 默认
    extract_candidate 会把它从 meta 排除, 导致 meta[extract_field] = None,
    BL-05 退化成抽 _id (与 extract_field=_id 等价, 链路彻底失语).
    解法: 上游 prequery 透传 extra_keep_fields=(extract_field,) 保字段.
    """
    upstream_keep = (
        (extract_field,) if extract_field in _LABEL_FIELDS else ()
    )
    upstream = await prequery_collection(
        namespace_id=namespace_id, collection=upstream_coll,
        fields=upstream_fields, pattern=upstream_pattern, database=database,
        extra_keep_fields=upstream_keep,
    )
    if upstream["status"] != "ok" or not upstream["candidates"]:
        return {
            "candidates": [], "total": 0,
            "status": "zero_hit_upstream",
            "upstream_count": upstream["total"],
            "upstream_ids": [],
        }
    # 抽链接键: _id 直接取 value, 其它字段从 meta 取
    upstream_ids_raw: list = []
    for c in upstream["candidates"]:
        if extract_field == "_id":
            v = c["value"]
        else:
            v = c.get("meta", {}).get(extract_field)
        if v:
            upstream_ids_raw.append(v)
    # M2: preserve-order dedup (dict 自 3.7 保插入顺序)
    upstream_ids = list(dict.fromkeys(upstream_ids_raw))

    threshold = settings.prequery_overflow_threshold
    db_ = await get_mongo_db(namespace_id=namespace_id, database=database)
    try:
        query = {downstream_filter_field: {"$in": upstream_ids}}
        cursor = db_[downstream_coll].find(query).limit(threshold + 1)
        docs = [d async for d in cursor]
    finally:
        close_db(db_)

    overflow = len(docs) > threshold
    kept = docs[:threshold]
    candidates = [extract_candidate(d) for d in kept]
    status = "overflow" if overflow else ("zero_hit" if not kept else "ok")
    out = {
        "candidates": candidates,
        "total": len(docs),
        "status": status,
        "upstream_count": upstream["total"],
        "upstream_ids": upstream_ids,
    }
    record_span_io(
        input={"namespace_id": namespace_id,
                "database": database,
                "upstream_coll": upstream_coll,
                "extract_field": extract_field,
                "downstream_coll": downstream_coll,
                "downstream_filter_field": downstream_filter_field},
        output={"status": status, "total": len(docs),
                 "upstream_count": upstream["total"]},
    )
    return out
