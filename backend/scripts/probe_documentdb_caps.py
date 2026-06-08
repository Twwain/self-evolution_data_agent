"""探测某 mongo 兼容数据源实测不支持的聚合算子/stage (证据驱动, 只读).

用法: cd backend && python -m scripts.probe_documentdb_caps [ds_id]
默认 ds_id=3 (DocumentDB). 只跑 aggregate + $limit:1 + maxTimeMS, 不写库;
$merge/$out 等写 stage 一律 SKIP。
"""
# ruff: noqa: E501 — 探测数据字典含密集 pipeline 字面量, 单行可读性优先
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.db.metadata import async_session
from app.engine.drivers.mongo import MongoDriver
from app.models.namespace import DataSource

# 判定"不支持"的错误特征 (errmsg 子串, 小写匹配)
_UNSUPPORTED_MARKERS = (
    "not supported", "unsupported", "unrecognized", "unknown",
    "not allowed", "cannot be used", "is not currently", "not currently support",
    "is unknown", "invalid pipeline", "feature not supported",
)


def _expr_probes() -> dict[str, dict]:
    """operator -> 一个 $project 内合法的表达式 (仅非支持才会失败)。"""
    return {
        "$getField": {"$getField": {"field": "x", "input": {"x": 1}}},
        "$setField": {"$setField": {"field": "x", "input": {"x": 1}, "value": 2}},
        "$unsetField": {"$unsetField": {"field": "x", "input": {"x": 1}}},
        "$function": {"$function": {"body": "function(){return 1}", "args": [], "lang": "js"}},
        "$regexMatch": {"$regexMatch": {"input": "abc", "regex": "a"}},
        "$regexFind": {"$regexFind": {"input": "abc", "regex": "a"}},
        "$regexFindAll": {"$regexFindAll": {"input": "abc", "regex": "a"}},
        "$dateAdd": {"$dateAdd": {"startDate": "$$NOW", "unit": "day", "amount": 1}},
        "$dateSubtract": {"$dateSubtract": {"startDate": "$$NOW", "unit": "day", "amount": 1}},
        "$dateDiff": {"$dateDiff": {"startDate": "$$NOW", "endDate": "$$NOW", "unit": "day"}},
        "$dateTrunc": {"$dateTrunc": {"date": "$$NOW", "unit": "day"}},
        "$dateToParts": {"$dateToParts": {"date": "$$NOW"}},
        "$dateFromString": {"$dateFromString": {"dateString": "2020-01-01"}},
        "$dateFromParts": {"$dateFromParts": {"year": 2020}},
        "$convert": {"$convert": {"input": "5", "to": "int"}},
        "$toString": {"$toString": 123},
        "$toInt": {"$toInt": "5"},
        "$toDecimal": {"$toDecimal": "5"},
        "$reduce": {"$reduce": {"input": [1, 2], "initialValue": 0, "in": {"$add": ["$$value", "$$this"]}}},
        "$map": {"$map": {"input": [1, 2], "as": "v", "in": {"$add": ["$$v", 1]}}},
        "$filter": {"$filter": {"input": [1, 2], "as": "v", "cond": {"$gt": ["$$v", 1]}}},
        "$zip": {"$zip": {"inputs": [[1], [2]]}},
        "$mergeObjects": {"$mergeObjects": [{"a": 1}, {"b": 2}]},
        "$objectToArray": {"$objectToArray": {"a": 1}},
        "$arrayToObject": {"$arrayToObject": [{"k": "a", "v": 1}]},
        "$switch": {"$switch": {"branches": [{"case": True, "then": 1}], "default": 0}},
        "$let": {"$let": {"vars": {"a": 1}, "in": {"$add": ["$$a", 1]}}},
        "$rand": {"$rand": {}},
        "$round": {"$round": [3.14, 1]},
        "$trunc": {"$trunc": [3.14, 1]},
        "$bitAnd": {"$bitAnd": [5, 3]},
        "$bitOr": {"$bitOr": [5, 3]},
        "$first": {"$first": [1, 2, 3]},
        "$last": {"$last": [1, 2, 3]},
        "$maxN": {"$maxN": {"input": [1, 2, 3], "n": 2}},
        "$minN": {"$minN": {"input": [1, 2, 3], "n": 2}},
        "$firstN": {"$firstN": {"input": [1, 2, 3], "n": 2}},
        "$lastN": {"$lastN": {"input": [1, 2, 3], "n": 2}},
        "$sortArray": {"$sortArray": {"input": [3, 1, 2], "sortBy": 1}},
        "$percentile": {"$percentile": {"input": [1, 2, 3], "p": [0.5], "method": "approximate"}},
        "$median": {"$median": {"input": [1, 2, 3], "method": "approximate"}},
        "$replaceOne": {"$replaceOne": {"input": "abc", "find": "a", "replacement": "x"}},
        "$replaceAll": {"$replaceAll": {"input": "aa", "find": "a", "replacement": "x"}},
        "$trim": {"$trim": {"input": " a "}},
        "$ltrim": {"$ltrim": {"input": " a "}},
        "$isNumber": {"$isNumber": 5},
        "$toHashedIndexKey": {"$toHashedIndexKey": "x"},
    }


def _stage_probes(coll: str) -> dict[str, list]:
    """stage -> 跟在 $limit:1 之后的合法 stage (仅非支持才会失败)。写 stage 不在此。"""
    return {
        "$lookup(basic)": [{"$lookup": {"from": coll, "localField": "_id", "foreignField": "_id", "as": "j"}}],
        "$lookup.let_pipeline": [{"$lookup": {"from": coll, "let": {"v": "$_id"}, "pipeline": [{"$limit": 1}], "as": "j"}}],
        "$unionWith": [{"$unionWith": {"coll": coll, "pipeline": [{"$limit": 1}]}}],
        "$facet": [{"$facet": {"a": [{"$limit": 1}]}}],
        "$bucketAuto": [{"$bucketAuto": {"groupBy": "$_id", "buckets": 1}}],
        "$graphLookup": [{"$graphLookup": {"from": coll, "startWith": "$_id", "connectFromField": "_id", "connectToField": "_id", "as": "g", "maxDepth": 0}}],
        "$setWindowFields": [{"$setWindowFields": {"sortBy": {"_id": 1}, "output": {"n": {"$sum": 1, "window": {"documents": ["unbounded", "current"]}}}}}],
        "$replaceWith": [{"$replaceWith": {"x": 1}}],
        "$sortByCount": [{"$sortByCount": "$_id"}],
        "$sample": [{"$sample": {"size": 1}}],
        "$densify": [{"$densify": {"field": "_n", "range": {"step": 1, "bounds": "full"}}}],
        "$redact": [{"$redact": "$$KEEP"}],
        "$count": [{"$count": "c"}],
    }


# 显式排除 — 会写库或需特殊环境, 绝不探测
_SKIPPED_STAGES = {
    "$merge": "写 stage — 安全起见不探测",
    "$out": "写 stage — 安全起见不探测",
    "$geoNear": "需 2dsphere 索引 — 跳过",
    "$search": "Atlas 专属 — 跳过",
}


def _classify(exc: Exception) -> tuple[str, int | None, str]:
    code = getattr(exc, "code", None)
    msg = str(getattr(exc, "details", None) or exc)
    low = msg.lower()
    if any(m in low for m in _UNSUPPORTED_MARKERS):
        return "unsupported", code, msg[:200]
    return "inconclusive", code, msg[:200]


async def _run_probe(coll_handle, pipeline: list) -> tuple[str, int | None, str]:
    try:
        cur = coll_handle.aggregate(pipeline, maxTimeMS=8000)
        async for _ in cur:
            break
        return "supported", None, ""
    except Exception as exc:  # noqa: BLE001
        return _classify(exc)


async def main(ds_id: int) -> None:
    async with async_session() as s:
        ds = (await s.execute(select(DataSource).where(DataSource.id == ds_id))).scalar_one_or_none()
    if ds is None:
        print(f"ds={ds_id} not found")
        return

    drv = MongoDriver()
    client = drv._get_client(ds)
    db = client[ds.database]
    info = await client.admin.command("buildInfo")
    print(f"=== ds={ds_id} db={ds.database} version={info.get('version')} "
          f"gitVersion={info.get('gitVersion')} modules={info.get('modules')} ===")

    names = await db.list_collection_names()
    if not names:
        print("无集合, 无法探测")
        return
    coll_name = names[0]
    coll = db[coll_name]
    print(f"base collection: {coll_name}\n")

    results: dict[str, list[str]] = {"supported": [], "unsupported": [], "inconclusive": []}
    details: list[str] = []

    # 表达式算子
    for op, expr in _expr_probes().items():
        verdict, code, msg = await _run_probe(coll, [{"$limit": 1}, {"$project": {"_p": expr}}])
        results[verdict].append(op)
        if verdict != "supported":
            details.append(f"  {op:24} [{verdict}] code={code} {msg}")

    # stage
    for name, stages in _stage_probes(coll_name).items():
        verdict, code, msg = await _run_probe(coll, [{"$limit": 1}, *stages])
        results[verdict].append(name)
        if verdict != "supported":
            details.append(f"  {name:24} [{verdict}] code={code} {msg}")

    # $documents (只能做首 stage)
    verdict, code, msg = await _run_probe(coll, [{"$documents": [{"x": 1}]}])
    results[verdict].append("$documents")
    if verdict != "supported":
        details.append(f"  {'$documents':24} [{verdict}] code={code} {msg}")

    print("=== SKIPPED (未探测) ===")
    for k, why in _SKIPPED_STAGES.items():
        print(f"  {k:24} {why}")

    for bucket in ("unsupported", "inconclusive", "supported"):
        items = sorted(results[bucket])
        print(f"\n=== {bucket.upper()} ({len(items)}) ===")
        print("  " + ", ".join(items) if items else "  (none)")

    print("\n=== 错误详情 (unsupported + inconclusive) ===")
    print("\n".join(details) if details else "  (none)")

    await drv.close_all()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 3))
