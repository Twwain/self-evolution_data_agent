"""SchemaCanonicalAuditLog 写入 helper.

与 app/knowledge/audit.py (KnowledgeAuditLog) 隔离, 因为审计语义不同:
- KnowledgeAuditLog: 管 KE 状态机 (proposed/canonical/...)
- SchemaCanonicalAuditLog: 管 candidate→canonical 流转
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SchemaAuditAction, SchemaCanonicalAuditLog


async def write_canonical_audit_log(
    db: AsyncSession,
    *,
    namespace_id: int,
    action: SchemaAuditAction,
    candidate_id: int | None = None,
    conflict_id: int | None = None,
    canonical_id: int | None = None,
    field_path: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
    actor_id: int | None = None,
    extra: dict[str, Any] | None = None,
) -> int:
    """单事务内追加一条审计.

    返回新 log id. 调用方负责 commit (我们 flush 拿 id 但不 commit, 让外层组装事务).
    """
    log = SchemaCanonicalAuditLog(
        namespace_id=namespace_id,
        action=action,
        candidate_id=candidate_id,
        conflict_id=conflict_id,
        canonical_id=canonical_id,
        field_path=field_path,
        before_json=json.dumps(before, ensure_ascii=False) if before is not None else None,
        after_json=json.dumps(after, ensure_ascii=False) if after is not None else None,
        reason=reason,
        actor_id=actor_id,
        extra_json=json.dumps(extra, ensure_ascii=False) if extra is not None else None,
    )
    db.add(log)
    await db.flush()
    return log.id
