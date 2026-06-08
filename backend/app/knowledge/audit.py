"""knowledge_entries 状态变更 + 编辑 audit 写入工具."""

import asyncio
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.intake import CONFLICT_CANDIDATE_LIMIT, detect_conflicts
from app.models import KnowledgeEntry
from app.models.knowledge_audit_log import KnowledgeAuditLog

if TYPE_CHECKING:
    from app.schemas import ConflictItemOut


async def write_audit(
    db: AsyncSession,
    entry_id: int | None,
    action: str,
    to_status: str,
    from_status: str | None = None,
    actor_id: int | None = None,
    reason: str = "",
    diff: dict[str, Any] | None = None,
) -> KnowledgeAuditLog:
    """写一条 audit_log.

    Important:
        本函数仅 db.add(), 不 commit/flush. 调用方必须在同一事务内
        与业务变更一起 commit, 否则:
          - 业务变更若 rollback, audit 也回滚 (期望行为);
          - 调用方忘记 commit, audit 静默丢失 (危险!).

        标准模式:
            async with db.begin():
                entry.status = "canonical"
                await write_audit(db, entry.id, action="approve",
                                   from_status="proposed", to_status="canonical")
            # __aexit__ 自动 commit
    """
    log = KnowledgeAuditLog(
        entry_id=entry_id,
        actor_id=actor_id,
        action=action,
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        diff_json=json.dumps(diff or {}, ensure_ascii=False),
    )
    db.add(log)
    return log


async def list_audit_logs(
    db: AsyncSession,
    entry_id: int,
) -> Sequence[KnowledgeAuditLog]:
    """按时间序列取某 entry 的 audit_log."""
    rows = await db.scalars(
        select(KnowledgeAuditLog)
        .where(KnowledgeAuditLog.entry_id == entry_id)
        .order_by(KnowledgeAuditLog.created_at)
    )
    return list(rows)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ 编辑后冲突检测 — Stage 3 Task 7 落地真实 LLM 调用                  ║
# ║ 复用 intake.detect_conflicts (生产路径), to_thread 包 sync LLM 调  ║
# ╚══════════════════════════════════════════════════════════════════╝
async def detect_conflict_against_canonical(
    db: AsyncSession,
    namespace_id: int | None,
    entry_type: str,
    content: str,
    *,
    exclude_entry_id: int | None = None,
) -> list["ConflictItemOut"]:
    """新内容 vs 同 namespace + 同 entry_type 的现有 canonical 条目对比 (LLM).

    候选集: status=canonical AND entry_type=given AND (namespace_id=given OR IS NULL).
    exclude_entry_id 给定时排除自身 (PUT 编辑场景), 防止与自己比对触发幻象冲突.
    候选硬上限走 intake.CONFLICT_CANDIDATE_LIMIT (防 prompt 爆炸).
    detect_conflicts 同步函数走 asyncio.to_thread, 不阻 event loop.
    """
    from app.schemas import ConflictItemOut  # 延迟导入断循环依赖

    stmt = select(KnowledgeEntry).where(
        KnowledgeEntry.entry_type == entry_type,
        KnowledgeEntry.status == "canonical",
    ).where(
        (KnowledgeEntry.namespace_id == namespace_id)
        | (KnowledgeEntry.namespace_id.is_(None))
    )
    if exclude_entry_id is not None:
        stmt = stmt.where(KnowledgeEntry.id != exclude_entry_id)
    stmt = stmt.limit(CONFLICT_CANDIDATE_LIMIT)

    rows = (await db.scalars(stmt)).all()
    if not rows:
        return []

    existing = [{"id": r.id, "content": r.content} for r in rows]
    report = await asyncio.to_thread(detect_conflicts, content, existing)
    return [
        ConflictItemOut(
            existing_id=item.existing_id,
            reason=item.reason,
            suggested=item.suggested,
        )
        for item in report.items
    ]
