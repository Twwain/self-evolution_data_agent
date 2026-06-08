"""Q-MQL 提取后台 — 扫描 query_history 成功记录, LLM refine → KE[type=example, status=proposed].

用法:
    cd backend && python -m scripts.extract_query_examples_from_history [--dry-run] [--ns <slug>]

配置:
    IS_QMQL_EXTRACT_INTERVAL_HOURS        (24)  — 建议 cron 按此频率运行
    IS_QMQL_EXTRACT_MIN_SUCCESS_AGE_HOURS (1)   — 候选 QueryHistory 最小冷却时间
    IS_QMQL_EXTRACT_MAX_PER_RUN           (50)  — 单次最多提取条数
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.metadata import async_session
from app.engine.llm import chat_completion
from app.models import KnowledgeEntry, Namespace, QueryHistory
from app.schemas.knowledge_payload import ExamplePayload

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════════
#  内部查询助手
# ════════════════════════════════════════════════════════════════════════════

async def _find_extractable_rows(
    db: AsyncSession,
    limit: int,
    min_age_hours: int,
    namespace_id: int | None = None,
) -> list[QueryHistory]:
    """返回满足提取条件的 QueryHistory 行:
    - row_count > 0 (查询有结果)
    - error 为空 (无错误)
    - generated_query 非空 (有可用 MQL)
    - created_at < now - min_age_hours (冷却完成)
    """
    # SQLite server_default 用本地时间 (datetime('now','localtime')), 用 datetime.now() 对齐
    cutoff = datetime.now() - timedelta(hours=min_age_hours)
    # ── 先组装所有 filters, 再 order_by + limit, 避免先截断再过滤漏掉目标行 ──
    filters = [
        QueryHistory.row_count > 0,
        QueryHistory.error == "",
        QueryHistory.generated_query != "",
        QueryHistory.created_at < cutoff,
        QueryHistory.role == "user",  # 只提取用户自然语言问题, 非 assistant 生成文本
    ]
    if namespace_id is not None:
        filters.append(QueryHistory.namespace_id == namespace_id)
    q = (
        select(QueryHistory)
        .where(*filters)
        .order_by(QueryHistory.created_at.desc())
        .limit(limit)
    )
    return list((await db.execute(q)).scalars().all())


async def _already_extracted(db: AsyncSession, namespace_id: int, question: str) -> bool:
    """检查是否已存在同问题的 qmql_extract example KE, 防重复提取."""
    row = await db.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.namespace_id == namespace_id,
            KnowledgeEntry.content == question,
            KnowledgeEntry.entry_type == "example",
            KnowledgeEntry.source == "qmql_extract",
        ).limit(1)
    )
    return row.scalar_one_or_none() is not None


# ════════════════════════════════════════════════════════════════════════════
#  LLM Refine — 原始 MQL → 结构化 ExamplePayload
# ════════════════════════════════════════════════════════════════════════════

_REFINE_PROMPT = """\
You are a query librarian. Given a natural language question and its MongoDB query JSON,
produce a JSON object matching ExamplePayload schema with these fields:
  question        (string)        — the original question
  target_collection (string)     — primary MongoDB collection name
  target_database (string|null)  — database name if identifiable, else null
  query_json      (object)        — parsed MongoDB query as JSON object
  result_summary  (string)        — brief description of result shape, empty string if unknown
  source_query_history_id (null) — always null (filled by caller)
  schema_hash     (null)          — always null

Output ONLY valid JSON, no markdown, no extra keys.

Question: {question}
MQL Query: {raw_query}
"""


def _refine_to_example_payload(question: str, raw_query: str) -> ExamplePayload:
    """调 LLM 把原始 MQL 字符串精炼成结构化 ExamplePayload."""
    prompt = _REFINE_PROMPT.format(question=question, raw_query=raw_query)
    raw_resp = chat_completion([{"role": "user", "content": prompt}])
    try:
        data = json.loads(raw_resp)
    except json.JSONDecodeError:
        log.warning("LLM refine 返回非 JSON, fallback 使用最小字段: question=%r", question)
        # 尝试把 raw_query 解析为 dict, 失败则放空 dict
        try:
            query_obj = json.loads(raw_query)
        except json.JSONDecodeError:
            query_obj = {}
        data = {
            "question": question,
            "target_collection": "",
            "target_database": None,
            "query_json": query_obj,
            "result_summary": "",
            "source_query_history_id": None,
            "schema_hash": None,
        }
    return ExamplePayload(**data)


# ════════════════════════════════════════════════════════════════════════════
#  主提取逻辑 (内层, 接受已有 db session)
# ════════════════════════════════════════════════════════════════════════════

async def _run_extraction_inner(
    db: AsyncSession,
    namespace_id: int | None,
    dry_run: bool,
) -> dict:
    """核心提取循环, 与 session 生命周期解耦便于测试注入."""
    stats = {"scanned": 0, "skipped_dup": 0, "written": 0, "errors": 0}

    rows = await _find_extractable_rows(
        db,
        limit=settings.qmql_extract_max_per_run,
        min_age_hours=settings.qmql_extract_min_success_age_hours,
        namespace_id=namespace_id,
    )
    stats["scanned"] = len(rows)

    for row in rows:
        try:
            if await _already_extracted(db, row.namespace_id, row.content):
                stats["skipped_dup"] += 1
                continue

            payload = _refine_to_example_payload(row.content, row.generated_query)

            if not dry_run:
                entry = KnowledgeEntry(
                    namespace_id=row.namespace_id,
                    content=row.content,
                    description=row.content,
                    entry_type="example",
                    tier="normal",
                    status="proposed",
                    source="qmql_extract",
                    payload=payload.model_dump_json(),
                )
                db.add(entry)
                await db.flush()
                stats["written"] += 1
            else:
                log.info(
                    "[dry-run] would write example KE: ns=%s q=%r",
                    row.namespace_id, row.content[:60],
                )
        except Exception as e:
            log.warning("extraction error history_id=%s: %s", row.id, e)
            stats["errors"] += 1

    if not dry_run:
        await db.commit()

    return stats


# ════════════════════════════════════════════════════════════════════════════
#  公开入口
# ════════════════════════════════════════════════════════════════════════════

async def run_extraction(
    dry_run: bool = False,
    namespace_slug: str | None = None,
    _db_override: AsyncSession | None = None,  # 仅测试注入, 生产代码不传
) -> dict:
    """扫描 QueryHistory 提取 Q-MQL example KE.

    Args:
        dry_run: True 时只扫描不写库, 用于预览或 CI 校验.
        namespace_slug: 指定命名空间 slug; None 则处理所有命名空间.
        _db_override: 测试专用 — 注入外部 AsyncSession, 跳过 async_session() 工厂.
    """
    # ── 测试注入路径: 直接用外部 session, 不开新连接 ──
    if _db_override is not None:
        namespace_id = await _resolve_namespace_id(_db_override, namespace_slug)
        if namespace_slug and namespace_id is None:
            log.error("Namespace %r not found", namespace_slug)
            return {"scanned": 0, "skipped_dup": 0, "written": 0, "errors": 0}
        return await _run_extraction_inner(_db_override, namespace_id, dry_run)

    # ── 生产路径: 打开独立 session ──
    async with async_session() as db:
        namespace_id = await _resolve_namespace_id(db, namespace_slug)
        if namespace_slug and namespace_id is None:
            log.error("Namespace %r not found", namespace_slug)
            return {"scanned": 0, "skipped_dup": 0, "written": 0, "errors": 0}
        return await _run_extraction_inner(db, namespace_id, dry_run)


async def _resolve_namespace_id(db: AsyncSession, slug: str | None) -> int | None:
    """slug → namespace_id; slug=None 则返 None (处理全部)."""
    if slug is None:
        return None
    row = (await db.execute(
        select(Namespace).where(Namespace.slug == slug)
    )).scalar_one_or_none()
    return row.id if row else None


# ════════════════════════════════════════════════════════════════════════════
#  CLI 入口
# ════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="Q-MQL 提取后台: query_history → KE[example]")
    parser.add_argument("--dry-run", action="store_true", help="只扫描不写库")
    parser.add_argument("--ns", help="namespace slug (不传则处理所有)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    result = asyncio.run(run_extraction(dry_run=args.dry_run, namespace_slug=args.ns))
    print(f"Q-MQL extraction done: {result}")


if __name__ == "__main__":
    main()
