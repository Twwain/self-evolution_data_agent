"""Stage 2 抓手 E — agent_traces 365 天 retention cleanup."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import delete

from app.config import settings
from app.db.metadata import async_session
from app.models import AgentTrace

log = logging.getLogger(__name__)


async def cleanup_once() -> int:
    """DELETE agent_traces older than retention_days. Returns deleted count."""
    threshold = datetime.now() - timedelta(days=settings.agent_trace_retention_days)
    async with async_session() as db:
        res = await db.execute(
            delete(AgentTrace).where(AgentTrace.created_at < threshold)
        )
        deleted: int = getattr(res, "rowcount", 0) or 0
        await db.commit()
        log.info("[agent_trace_cleanup] deleted=%d threshold=%s", deleted, threshold)
        return deleted


async def cleanup_loop() -> None:
    """Background loop — runs cleanup_once every 24h."""
    while True:
        try:
            await cleanup_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("[agent_trace_cleanup_loop] err: %s", e)
        try:
            await asyncio.sleep(86400)  # 每天一次
        except asyncio.CancelledError:
            raise
