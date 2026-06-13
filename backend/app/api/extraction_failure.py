"""Extraction failure log API — 列表 / 重试 / 忽略."""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import assert_ns_access, require_admin_or_above, require_ns_manage
from app.db.metadata import get_db
from app.models.extraction_failure_log import ExtractionFailureLog
from app.models.user import User

router = APIRouter(tags=["extraction-failures"])
log = logging.getLogger(__name__)


@router.get("/api/namespaces/{ns_id}/extraction-failures")
async def list_extraction_failures(
    ns_id: int,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    """列出命名空间下的抽取失败记录."""
    rows = (await db.execute(
        select(ExtractionFailureLog)
        .where(ExtractionFailureLog.namespace_id == ns_id)
        .order_by(ExtractionFailureLog.last_seen_at.desc())
        .limit(200)
    )).scalars().all()

    return [
        {
            "id": r.id,
            "extraction_kind": r.extraction_kind,
            "failure_type": r.failure_type,
            "source_file": r.source_file,
            "source_mapper": r.source_mapper,
            "source_method": r.source_method,
            "source_content": r.source_content,
            "failure_message": r.failure_message,
            "retry_count": r.retry_count,
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "failure_extra": json.loads(r.failure_extra_json)
            if r.failure_extra_json else None,
        }
        for r in rows
    ]


@router.post("/api/extraction-failures/{failure_id}/retry")
async def retry_extraction_failure(
    failure_id: int,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """标记重试 (递增 retry_count, 更新 last_seen_at)."""
    row = await db.get(ExtractionFailureLog, failure_id)
    if not row:
        raise HTTPException(404, "记录不存在")
    await assert_ns_access(db, actor, row.namespace_id)
    row.retry_count += 1
    row.last_seen_at = datetime.now()
    await db.commit()
    return {"status": "queued", "retry_count": row.retry_count}


@router.post("/api/extraction-failures/{failure_id}/ignore")
async def ignore_extraction_failure(
    failure_id: int,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """忽略 (物理删除该记录)."""
    row = await db.get(ExtractionFailureLog, failure_id)
    if not row:
        raise HTTPException(404, "记录不存在")
    await assert_ns_access(db, actor, row.namespace_id)
    await db.delete(row)
    await db.commit()
    return {"status": "ignored"}
