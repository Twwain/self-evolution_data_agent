"""术语手动刷新 API — 从训练管道解耦后的独立入口.

用户在 Schema 校对页面解决完冲突后, 手动触发术语全量重新提取.
流程: 清除 ns 下所有 source=schema 的 terminology KE → 全量抽词 → 写入.
"""
from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db.metadata import async_session, get_db
from app.knowledge.terminology_refresher import refresh_namespace_terminology
from app.knowledge.terminology_vectors import delete_terminology_vectors
from app.models import KnowledgeEntry, Namespace
from app.models.terminology_conflict import TerminologyConflict
from app.models.user import User

router = APIRouter(
    prefix="/api/namespaces/{ns_id}/terminology",
    tags=["terminology"],
)
log = logging.getLogger(__name__)

# 进程内进度追踪 (单 worker 假设)
_refresh_tasks: dict[str, dict] = {}


@router.post("/refresh")
async def refresh_terminology(
    ns_id: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """手动触发术语全量重新提取.

    1. 清除该 ns 下所有 entry_type=terminology AND source=schema 的 KE (不区分 status)
    2. 清除对应的 ChromaDB 向量
    3. 清除 source=schema 的 open TerminologyConflict
    4. 异步执行全量术语抽取
    """
    ns = await db.get(Namespace, ns_id)
    if not ns:
        raise HTTPException(404, "命名空间不存在")

    # 检查是否有正在进行的刷新任务
    for task_id, info in _refresh_tasks.items():
        if info.get("ns_id") == ns_id and info.get("status") == "running":
            return {"task_id": task_id, "status": "already_running"}

    task_id = uuid4().hex[:12]
    _refresh_tasks[task_id] = {
        "ns_id": ns_id,
        "status": "running",
        "progress": 0,
        "message": "清除历史术语...",
    }

    asyncio.create_task(_run_refresh(task_id, ns_id, ns.slug))
    return {"task_id": task_id, "status": "started"}


@router.get("/refresh/{task_id}")
async def get_refresh_progress(
    ns_id: int,
    task_id: str,
    admin: User = Depends(require_admin),
) -> dict:
    """查询术语刷新进度."""
    info = _refresh_tasks.get(task_id)
    if not info or info.get("ns_id") != ns_id:
        raise HTTPException(404, "任务不存在")
    return info


async def _run_refresh(task_id: str, ns_id: int, ns_slug: str) -> None:
    """后台执行: 清除 + 重新抽取."""
    info = _refresh_tasks[task_id]
    try:
        # ── Step 1: 清除历史 schema 术语 ──
        info["message"] = "清除历史术语..."
        info["progress"] = 10
        deleted_count = await _purge_schema_terminology(ns_id, ns_slug)
        log.info("[term_refresh] ns=%d 清除 schema 术语 %d 条", ns_id, deleted_count)

        # ── Step 2: 全量抽词 ──
        info["message"] = "正在提取术语..."
        info["progress"] = 30

        async with async_session() as db:
            report = await refresh_namespace_terminology(db, ns_id)
        if report.skipped:
            info["status"] = "completed"
            info["progress"] = 100  # noqa: hardcode
            info["message"] = "无业务术语数据(无 canonical), 跳过抽取"
            info["result"] = {"inserted": 0, "failed": 0, "reason": report.reason}
            return

        info["status"] = "completed"
        info["progress"] = 100  # noqa: hardcode
        info["message"] = (
            f"完成: 新增 {len(report.merged)} 条术语"
            + (f", 失败 {len(report.failed)} 条" if report.failed else "")
        )
        info["result"] = {
            "inserted": len(report.merged),
            "failed": len(report.failed),
            "canonicals_seen": report.canonicals_seen,
        }
    except Exception as e:
        log.exception("[term_refresh] ns=%d 术语刷新失败: %s", ns_id, e)
        info["status"] = "failed"
        info["progress"] = 100  # noqa: hardcode
        info["message"] = f"术语提取失败: {e}"


async def _purge_schema_terminology(ns_id: int, ns_slug: str) -> int:
    """清除 ns 下所有 source=schema 的 terminology KE + 向量 + 冲突."""
    async with async_session() as db:
        # 查出所有要删的 KE
        ke_rows = list((await db.execute(
            select(KnowledgeEntry).where(
                KnowledgeEntry.namespace_id == ns_id,
                KnowledgeEntry.entry_type == "terminology",
                KnowledgeEntry.source == "schema",
            )
        )).scalars().all())

        # 删除 ChromaDB 向量
        for ke in ke_rows:
            try:
                delete_terminology_vectors(
                    slug=ns_slug, entry_id=ke.id, namespace_id=ns_id,
                )
            except Exception as e:
                log.warning("[term_refresh] 向量删除失败 ke=%d: %s", ke.id, e)

        # 删除 KE 行
        if ke_rows:
            await db.execute(
                delete(KnowledgeEntry).where(
                    KnowledgeEntry.namespace_id == ns_id,
                    KnowledgeEntry.entry_type == "terminology",
                    KnowledgeEntry.source == "schema",
                )
            )

        # 清除 source=schema 的 open TerminologyConflict
        await db.execute(
            delete(TerminologyConflict).where(
                TerminologyConflict.namespace_id == ns_id,
                TerminologyConflict.candidate_source == "schema",
                TerminologyConflict.status == "open",
            )
        )

        await db.commit()
        return len(ke_rows)
