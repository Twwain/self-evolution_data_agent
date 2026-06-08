"""Phase 1c Task 1.6 — terminology 唯一键冲突解决 API.

# ════════════════════════════════════════════
#  契约
# ════════════════════════════════════════════
# POST /api/namespaces/{ns_id}/terminology/conflicts/{cid}/resolve
#
# 5 种 resolution_choice:
#   - keep_existing: 保留现状, 拒掉 candidate, 不动 existing
#   - replace:       existing 标 is_superseded=True, candidate 作新 KE 入 proposed
#   - merge_both:    existing.synonyms ∪ {candidate.term, *candidate.synonyms}
#   - reject_both:   existing.status='rejected'
#   - manual_edit:   就地改 existing.payload + 翻 canonical, candidate 丢弃
#                    edited_payload 必填; 路由三元组 (database/collection/db_type)
#                    锁定与 existing 一致, 改了 → 422
#
# 跨 ns 越权 → 404. 已 resolved → 409. choice 不在白名单 → 422 (Pydantic Literal).
# audit_log 全程留痕 (actor_id=admin.id), 不含 namespace_id 字段 (model 没这列).
"""

import json
import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db.metadata import get_db
from app.models.knowledge_audit_log import KnowledgeAuditLog
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import Namespace
from app.models.terminology_conflict import TerminologyConflict
from app.models.user import User
from app.schemas.knowledge_payload import TerminologyPayload

router = APIRouter(
    prefix="/api/namespaces/{ns_id}/terminology/conflicts",
    tags=["terminology-conflict"],
)
log = logging.getLogger(__name__)


class ResolveBody(BaseModel):
    resolution_choice: Literal[
        "keep_existing", "replace", "merge_both", "reject_both", "manual_edit",
    ]
    edited_payload: dict[str, Any] | None = None  # manual_edit 专用


# ════════════════════════════════════════════
#  Phase 3 Task 3.3: GET 列表端点 (前端 TerminologyConflictModal 入口)
# ════════════════════════════════════════════
@router.get("", status_code=200)
async def list_conflicts(
    ns_id: int,
    status: str = "open",
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
) -> dict:
    """List terminology conflicts under a namespace, default status='open'.

    带 existing_payload (KnowledgeEntry.payload JSON 解析) 让前端 modal 直接渲染
    现有条目的 database/collection/db_type 路由元数据, 不必再二次拉 KE.
    """
    rows = (await db.execute(
        select(TerminologyConflict)
        .where(
            TerminologyConflict.namespace_id == ns_id,
            TerminologyConflict.status == status,
        )
        .order_by(TerminologyConflict.created_at.desc())
    )).scalars().all()

    # 批量回查 existing KE payload — N+1 改成单次 IN 查询
    existing_ids = list({c.existing_entry_id for c in rows})
    existing_map: dict[int, dict] = {}
    if existing_ids:
        kes = (await db.execute(
            select(KnowledgeEntry).where(KnowledgeEntry.id.in_(existing_ids))
        )).scalars().all()
        for ke in kes:
            try:
                existing_map[ke.id] = json.loads(ke.payload or "{}")
            except json.JSONDecodeError:
                existing_map[ke.id] = {}

    return {
        "conflicts": [
            {
                "id": c.id,
                "namespace_id": c.namespace_id,
                "existing_entry_id": c.existing_entry_id,
                "existing_payload": existing_map.get(c.existing_entry_id),
                "candidate_payload": c.candidate_payload,
                "candidate_source": c.candidate_source,
                "candidate_repo_id": c.candidate_repo_id,
                "status": c.status,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in rows
        ]
    }


# ════════════════════════════════════════════
#  4 choice 内部 helper (各自纯写 SQLite, 调用方 commit)
# ════════════════════════════════════════════
async def _keep_existing(
    db: AsyncSession, existing: KnowledgeEntry, candidate: dict, actor: User,
) -> None:
    db.add(KnowledgeAuditLog(
        entry_id=existing.id, actor_id=actor.id,
        action="reject", from_status=existing.status, to_status=existing.status,
        reason=f"conflict_keep_existing candidate_term={candidate.get('term')!r}",
    ))


async def _replace(
    db: AsyncSession, ns_id: int, existing: KnowledgeEntry, candidate: dict,
    candidate_source: str, candidate_repo_id: int | None, actor: User,
) -> KnowledgeEntry:
    existing.is_superseded = True
    db.add(KnowledgeAuditLog(
        entry_id=existing.id, actor_id=actor.id,
        action="supersede", from_status=existing.status, to_status=existing.status,
        reason="conflict_resolved_replace",
    ))
    # Phase 1c review I-1: 保 candidate 真实血缘 — source/repo_id 由 conflict 行透传,
    # 不写死 manual. admin 点 "replace" 是治理动作, 但术语文本本身可能源自 git/agent_learn.
    new_ke = KnowledgeEntry(
        namespace_id=ns_id, entry_type="terminology",
        source=candidate_source,
        repo_id=candidate_repo_id,
        status="proposed", is_superseded=False,
        payload=json.dumps(candidate, ensure_ascii=False),
        content=candidate.get("term", ""),
    )
    db.add(new_ke)
    await db.flush()
    db.add(KnowledgeAuditLog(
        entry_id=new_ke.id, actor_id=actor.id,
        action="propose", from_status=None, to_status="proposed",
        reason=f"conflict_resolved_replace_new candidate_source={candidate_source}",
    ))
    return new_ke


async def _merge_both(
    db: AsyncSession, existing: KnowledgeEntry, candidate: dict, actor: User,
) -> None:
    existing_payload = TerminologyPayload(**json.loads(existing.payload))
    cand_term = candidate.get("term", "")
    cand_syns = list(candidate.get("synonyms") or [])
    merged = list(dict.fromkeys(
        s for s in (*existing_payload.synonyms, cand_term, *cand_syns)
        if s and s != existing_payload.term
    ))
    updated = existing_payload.model_copy(update={"synonyms": merged})
    existing.payload = updated.model_dump_json()
    db.add(KnowledgeAuditLog(
        entry_id=existing.id, actor_id=actor.id,
        action="merge", from_status=existing.status, to_status=existing.status,
        reason="conflict_merge_both",
        diff_json=json.dumps({
            "before": {"synonyms": existing_payload.synonyms},
            "after": {"synonyms": merged},
            "candidate_term": cand_term,
        }, ensure_ascii=False),
    ))


async def _reject_both(
    db: AsyncSession, existing: KnowledgeEntry, actor: User,
) -> None:
    from_status = existing.status
    existing.status = "rejected"
    db.add(KnowledgeAuditLog(
        entry_id=existing.id, actor_id=actor.id,
        action="reject", from_status=from_status, to_status="rejected",
        reason="conflict_resolved_reject_both",
    ))


# ════════════════════════════════════════════
#  manual_edit — 就地改 existing.payload + 翻 canonical, candidate 丢弃
# ════════════════════════════════════════════
async def _manual_edit(
    db: AsyncSession, existing: KnowledgeEntry, edited_payload: dict, actor: User,
) -> None:
    """就地编辑 existing → canonical. 路由三元组锁定与 existing 一致.

    流程:
      1. TerminologyPayload schema 校验 (term/synonyms shape, db_type literal)
      2. 路由三元组 (primary_database/primary_collection/db_type) 与 existing 一致校验
         — 用户不可借冲突解决跨表迁移
      3. existing.payload 覆写 + status → canonical (若已 canonical 则保持)
      4. audit_log 双轨: edit (diff_json) + 若 status 变化再追 approve
      调用方负责 ChromaDB upsert (走 existing.content/id, 不需要返回值)
    """
    if not edited_payload:
        raise HTTPException(422, "edited_payload required for manual_edit")
    try:
        new_payload = TerminologyPayload(**edited_payload)
    except ValidationError as e:
        raise HTTPException(422, f"edited_payload schema invalid: {e}") from e

    old_payload = TerminologyPayload(**json.loads(existing.payload))

    # 路由三元组锁定校验 — 任一字段变化即 422 拒绝
    if (new_payload.primary_database != old_payload.primary_database
            or new_payload.primary_collection != old_payload.primary_collection
            or new_payload.db_type != old_payload.db_type):
        raise HTTPException(
            422,
            "routing tuple (primary_database/primary_collection/db_type) "
            "must match existing — manual_edit 不允许跨表迁移",
        )

    # 就地覆写
    from_status = existing.status
    existing.payload = new_payload.model_dump_json()
    existing.content = new_payload.term

    # audit: edit (always)
    db.add(KnowledgeAuditLog(
        entry_id=existing.id, actor_id=actor.id,
        action="edit", from_status=from_status, to_status=from_status,
        reason="manual_edit_during_conflict",
        diff_json=json.dumps({
            "before": old_payload.model_dump(),
            "after": new_payload.model_dump(),
        }, ensure_ascii=False),
    ))

    # 翻 canonical (若需要)
    if from_status != "canonical":
        existing.status = "canonical"
        existing.reviewed_at = datetime.now()
        existing.reviewed_by_id = actor.id
        db.add(KnowledgeAuditLog(
            entry_id=existing.id, actor_id=actor.id,
            action="approve", from_status=from_status, to_status="canonical",
            reason="manual_edit_during_conflict",
        ))


# ════════════════════════════════════════════
#  主 endpoint
# ════════════════════════════════════════════
@router.post("/{cid}/resolve", status_code=200)
async def resolve_conflict(
    ns_id: int,
    cid: int,
    body: ResolveBody,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_admin),
) -> dict:
    conflict = (await db.execute(
        select(TerminologyConflict).where(
            TerminologyConflict.id == cid,
            TerminologyConflict.namespace_id == ns_id,
        )
    )).scalar_one_or_none()
    if conflict is None:
        raise HTTPException(404, "conflict not found in this namespace")
    if conflict.status != "open":
        raise HTTPException(409, "conflict already resolved")

    existing = await db.get(KnowledgeEntry, conflict.existing_entry_id)
    if existing is None:
        raise HTTPException(404, "existing entry missing")
    candidate = json.loads(conflict.candidate_payload)
    choice = body.resolution_choice

    if choice == "keep_existing":
        await _keep_existing(db, existing, candidate, actor)
    elif choice == "replace":
        await _replace(
            db, ns_id, existing, candidate,
            candidate_source=conflict.candidate_source,
            candidate_repo_id=conflict.candidate_repo_id,
            actor=actor,
        )
    elif choice == "merge_both":
        await _merge_both(db, existing, candidate, actor)
    elif choice == "manual_edit":
        if body.edited_payload is None:
            raise HTTPException(422, "edited_payload required for manual_edit")
        await _manual_edit(db, existing, body.edited_payload, actor)
    else:  # reject_both — Literal 收紧后 5 取 1, 无 default fallthrough 风险
        await _reject_both(db, existing, actor)

    conflict.status = "resolved"
    conflict.resolution_choice = choice
    conflict.resolved_at = datetime.now()
    conflict.resolved_by_id = actor.id
    await db.commit()

    # ── manual_edit 走 ChromaDB upsert (canonical 进 RAG); 其他 choice 不动 vector ──
    if choice == "manual_edit":
        ns = await db.get(Namespace, ns_id)
        if ns is not None:
            try:
                import asyncio

                from app.knowledge.knowledge_retriever import (
                    parse_entry_payload,
                    upsert_knowledge_entry,
                )
                await asyncio.to_thread(
                    upsert_knowledge_entry,
                    ns.slug, existing.id, existing.content,
                    tier=existing.tier, namespace_id=ns_id,
                    entry_type="terminology", status="canonical",
                    payload=parse_entry_payload(existing.payload),
                )
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "[terminology_conflict] chromadb upsert failed cid=%d: %s",
                    cid, e,
                )

    # ── AC 自动机失效 + 重建 (所有 choice 都可能影响 canonical terminology) ──
    # keep_existing: 无变化, 但成本低不值得分支判断
    # replace: existing superseded
    # merge_both: existing.synonyms 变化
    # manual_edit: existing 可能翻 canonical + payload 变化
    # reject_both: existing → rejected
    if choice != "keep_existing":
        try:
            from app.knowledge.terminology_automaton import invalidate, rebuild_all
            await invalidate(ns_id)
            await rebuild_all(db)
        except Exception as e:  # noqa: BLE001
            log.warning("[terminology_conflict] automaton invalidate failed: %s", e)

    log.info(
        "[terminology_conflict] resolved cid=%d ns=%d choice=%s actor=%s",
        cid, ns_id, choice, actor.username,
    )
    return {"id": cid, "status": "resolved", "choice": choice}
