"""
Pending TTL cleanup — 周期清理过期 pending_clarifications (Decomposer Routing P3)

设计:
- 后台 asyncio 任务, 由 FastAPI lifespan 启动 / 取消
- 每 N 秒扫描 expires_at < now AND status IN ('pending','overflow') → 批量 DELETE
- 失败容忍: 异常只 log, 不中断循环 (后台任务 die 等于静默失败)

选项:
- 默认间隔由 settings.pending_cleanup_interval_secs 配置 (3600s)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import delete, func, select

from app.config import settings
from app.db.metadata import async_session
from app.models import PendingClarification

log = logging.getLogger(__name__)


async def _cleanup_once() -> int:
    """单次清理: 删掉所有 expires_at < now 的 pending, 返回删除条数."""
    now = datetime.now()
    async with async_session() as s:
        # 先统计供日志可见性
        cnt = await s.scalar(
            select(func.count(PendingClarification.id)).where(
                PendingClarification.expires_at < now,
            )
        )
        if not cnt:
            return 0
        # 删除 — status 不限, 过期就该清 (pending/resolved/abandoned/overflow 都可)
        await s.execute(
            delete(PendingClarification).where(
                PendingClarification.expires_at < now,
            )
        )
        await s.commit()
        return int(cnt)


async def pending_cleanup_loop(interval_secs: int | None = None) -> None:
    """
    后台周期任务 — FastAPI lifespan 启动时 create_task, 关闭时 cancel.

    异常策略:
    - CancelledError: 退出循环
    - 其他异常: log.error + 继续下一轮 (不让后台任务静默 die)
    """
    effective_interval: int = interval_secs if interval_secs is not None else int(
        getattr(settings, "pending_cleanup_interval_secs", 3600)
    )
    log.info("[pending_cleanup] 启动后台清理循环 interval=%ds", effective_interval)

    while True:
        try:
            deleted = await _cleanup_once()
            if deleted:
                log.info("[pending_cleanup] 已删除 %d 条过期 pending", deleted)
            else:
                log.debug("[pending_cleanup] 无过期 pending")
        except asyncio.CancelledError:
            log.info("[pending_cleanup] 收到 cancel, 退出循环")
            raise
        except Exception as e:
            log.error("[pending_cleanup] 循环异常 (下轮继续): %s", e, exc_info=True)

        try:
            await asyncio.sleep(effective_interval)
        except asyncio.CancelledError:
            log.info("[pending_cleanup] sleep 被 cancel, 退出循环")
            raise


__all__ = ["_cleanup_once", "pending_cleanup_loop"]
