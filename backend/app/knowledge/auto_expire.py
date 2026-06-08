"""Stage 3 Task 8 — proposed 知识超期自动转 rejected 后台任务.

设计模式参考 app/engine/pending_cleanup.py:
- FastAPI lifespan create_task / cancel
- 异常隔离 (CancelledError 退出, 其他 log + 继续)
- 单次清理 expire_stale_proposed(db) 抽离供测试直调
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.metadata import async_session
from app.knowledge.audit import write_audit
from app.models import KnowledgeEntry

log = logging.getLogger(__name__)


async def expire_stale_proposed(db: AsyncSession) -> int:
    """单次清理: WHERE status=proposed AND created_at < NOW - max_age_days → rejected.

    每条转 rejected 写一条 audit_log (action=expire, reason="auto-expired",
    actor_id=None 表示系统). 返回过期条数.
    """
    cutoff = datetime.now() - timedelta(days=settings.audit_proposed_max_age_days)
    rows = (await db.scalars(
        select(KnowledgeEntry).where(
            KnowledgeEntry.status == "proposed",
            KnowledgeEntry.created_at < cutoff,
        )
    )).all()
    if not rows:
        return 0
    for entry in rows:
        entry.status = "rejected"
        await write_audit(
            db, entry_id=entry.id, action="expire",
            from_status="proposed", to_status="rejected",
            actor_id=None, reason="auto-expired",
        )
    await db.commit()
    return len(rows)


async def proposed_auto_expire_loop(interval_secs: int | None = None) -> None:
    """后台周期任务. 异常隔离参考 pending_cleanup_loop."""
    effective: int = interval_secs if interval_secs is not None else (
        settings.audit_auto_expire_check_interval_hours * 3600
    )
    log.info("[auto_expire] 启动 proposed 自动过期循环 interval=%ds", effective)

    while True:
        try:
            async with async_session() as db:
                expired = await expire_stale_proposed(db)
            if expired:
                log.info("[auto_expire] 已转 rejected %d 条超期 proposed", expired)
            else:
                log.debug("[auto_expire] 无超期 proposed")
        except asyncio.CancelledError:
            log.info("[auto_expire] 收到 cancel, 退出循环")
            raise
        except Exception as e:
            log.error("[auto_expire] 循环异常 (下轮继续): %s", e, exc_info=True)

        try:
            await asyncio.sleep(effective)
        except asyncio.CancelledError:
            log.info("[auto_expire] sleep 被 cancel, 退出循环")
            raise


__all__ = ["expire_stale_proposed", "proposed_auto_expire_loop"]
