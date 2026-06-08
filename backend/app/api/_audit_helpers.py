"""Stage 3 审核 API 共用 helper — 9 端点复用基础.

包含:
    - apply_entry_filters / paginate_count: 队列分页 + 过滤模板
    - resolve_ns_slug: namespace_id → ChromaDB collection slug (None → GLOBAL_NS_SLUG)
    - chroma_upsert_safe / chroma_delete_safe: best-effort ChromaDB 同步, 失败仅 log.warning
    - apply_inline_edits: approve / PUT 时通用 inline 编辑字段白名单 (content/payload/tier)

Why 抽离: list_audit_queue / approve / reject / restore / PUT edit / DELETE / conflict-preview
共用模板, 不抽就 9 处复制. Linus-style: 第二次出现就抽.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.knowledge_retriever import (
    GLOBAL_NS_SLUG,
    delete_knowledge_entry,
    parse_entry_payload,
    upsert_knowledge_entry,
)
from app.models import KnowledgeEntry, Namespace

log = logging.getLogger(__name__)

ALLOWED_EDIT_FIELDS = ("content", "payload", "tier", "hypothetical_queries")
"""approve / PUT edit 通用编辑白名单 — 别处不该再硬编码这些字段."""


def apply_entry_filters(
    stmt: Select,
    *,
    namespace_id: int | None = None,
    entry_type: str | None = None,
    status: str | None = None,
    source: str | None = None,
    q: str | None = None,
) -> Select:
    """对 KnowledgeEntry select 追加 5 维过滤. None / 空串表示不过滤.

    q: 关键词模糊匹配 content + description + payload 三字段 OR.
       SQLite ILIKE 大小写无关; payload 是 JSON Text 直接 LIKE 命中内嵌字段值.
    """
    if namespace_id is not None:
        stmt = stmt.where(KnowledgeEntry.namespace_id == namespace_id)
    if entry_type is not None:
        stmt = stmt.where(KnowledgeEntry.entry_type == entry_type)
    if status is not None:
        stmt = stmt.where(KnowledgeEntry.status == status)
    if source is not None:
        stmt = stmt.where(KnowledgeEntry.source == source)
    if q:
        pat = f"%{q}%"
        stmt = stmt.where(or_(
            KnowledgeEntry.content.ilike(pat),
            KnowledgeEntry.description.ilike(pat),
            KnowledgeEntry.payload.ilike(pat),
        ))
    return stmt


async def paginate_count(db: AsyncSession, stmt: Select) -> int:
    """对 select 计算总数 (用于分页 total). 子查询包装支持任意 where."""
    return (await db.scalar(select(func.count()).select_from(stmt.subquery()))) or 0


async def resolve_ns_slug(db: AsyncSession, namespace_id: int | None) -> str:
    """KnowledgeEntry.namespace_id → ChromaDB collection slug.

    namespace_id is None → GLOBAL_NS_SLUG (全局 KE 落 __global__ 集合).
    namespace_id 给定 → ns.slug; 若 ns 不存在 (race condition) 返 GLOBAL_NS_SLUG 兜底.
    """
    if namespace_id is None:
        return GLOBAL_NS_SLUG
    ns = await db.get(Namespace, namespace_id)
    if ns is None:
        log.warning("[audit] namespace_id=%d 已不存在, slug fallback __global__", namespace_id)
        return GLOBAL_NS_SLUG
    return ns.slug


def _parse_payload(raw: str | None) -> dict | None:
    """Thin wrapper → 公共 parse_entry_payload (保持局部调用不变)."""
    return parse_entry_payload(raw)


async def chroma_upsert_safe(slug: str, entry: KnowledgeEntry) -> None:
    """ChromaDB upsert best-effort — 失败仅 log.warning, 不阻业务.

    Phase 0 (2026-05-26): upsert_knowledge_entry 不再返 hq_list,
    rule/route_hint 走单向量路径. HQ 由 hq_writer 统一管理.
    """
    try:
        await asyncio.to_thread(
            upsert_knowledge_entry,
            slug=slug,
            entry_id=entry.id,
            content=entry.content,
            tier=entry.tier,
            namespace_id=entry.namespace_id,
            entry_type=entry.entry_type,
            status=entry.status,
            payload=_parse_payload(entry.payload),
        )
    except Exception as e:
        log.warning("[audit] ChromaDB upsert 失败 id=%d: %s", entry.id, e)


async def chroma_delete_safe(slug: str, entry: KnowledgeEntry) -> None:
    """ChromaDB delete best-effort — 失败仅 log.warning, 不阻业务.

    2026-05-11 修: 之前漏传 entry_type, terminology 类多向量条目走"单向量路径"删
    `ke_{id}` 主键删不到真实存的 `ke_{id}_0` / `ke_{id}_1`, 残留向量污染 RAG.
    """
    try:
        await asyncio.to_thread(
            delete_knowledge_entry,
            slug=slug,
            entry_id=entry.id,
            namespace_id=entry.namespace_id,
            entry_type=entry.entry_type,
        )
    except Exception as e:
        log.warning("[audit] ChromaDB delete 失败 id=%d: %s", entry.id, e)


def apply_inline_edits(entry: KnowledgeEntry, edits: dict | None) -> None:
    """approve / PUT 时通用 inline 编辑 — 仅接受 ALLOWED_EDIT_FIELDS 字段.

    hypothetical_queries 由 caller 走 hq_writer 路径, 此函数不处理.
    """
    if not edits:
        return
    for field in ALLOWED_EDIT_FIELDS:
        if field == "hypothetical_queries":
            continue  # 由 caller 走 hq_writer
        value = edits.get(field)
        if value is not None:
            setattr(entry, field, value)


async def apply_amem_evolution(
    db: AsyncSession, entry: KnowledgeEntry, actor_id: int,
) -> None:
    """Stage 2 抓手 D: A-MEM 演化触发 — equivalent/supplement/conflict cascade.

    在主 commit 前调用, 修改 related entries 的状态并写 audit_log.
    """
    import json
    from datetime import datetime

    from app.knowledge.audit import write_audit

    if not entry.related_entry_ids_json or entry.related_entry_ids_json == "[]":
        return

    try:
        related = json.loads(entry.related_entry_ids_json)
    except json.JSONDecodeError:
        return

    for r in related:
        old_id = r.get("related_entry_id")
        rel = r.get("relation")
        if not old_id or rel not in {"equivalent", "supplement", "conflict"}:
            continue
        old_ke = await db.get(KnowledgeEntry, old_id)
        if old_ke is None or old_ke.status != "canonical":
            continue  # 已被处理 (并发场景)

        if rel == "equivalent":
            old_ke.status = "superseded"
            old_ke.is_superseded = True
            old_ke.superseded_by = entry.id
            await write_audit(
                db, entry_id=old_ke.id, action="supersede",
                from_status="canonical", to_status="superseded",
                actor_id=actor_id,
                reason=f"A-MEM equivalent merge by ke#{entry.id}",
                diff={"merged_into": entry.id},
            )
        elif rel == "supplement":
            try:
                old_related = json.loads(old_ke.related_entry_ids_json or "[]")
            except json.JSONDecodeError:
                old_related = []
            old_related.append({
                "related_entry_id": entry.id,
                "relation": "supplement",
                "llm_reason": f"补充自 ke#{entry.id}",
                "detected_at": datetime.now().isoformat(),
            })
            old_ke.related_entry_ids_json = json.dumps(
                old_related, ensure_ascii=False,
            )
            await write_audit(
                db, entry_id=old_ke.id, action="edit",
                from_status="canonical", to_status="canonical",
                actor_id=actor_id,
                reason=f"A-MEM supplement link by ke#{entry.id}",
                diff={"added_link": entry.id},
            )
        elif rel == "conflict":
            old_ke.status = "rejected"
            await write_audit(
                db, entry_id=old_ke.id, action="reject",
                from_status="canonical", to_status="rejected",
                actor_id=actor_id,
                reason=f"A-MEM conflict overridden by ke#{entry.id}",
                diff={"overridden_by": entry.id},
            )


async def sync_amem_chroma_deletes(
    db: AsyncSession, entry: KnowledgeEntry,
) -> None:
    """Post-commit: delete ChromaDB vectors for superseded/rejected A-MEM targets."""
    import json

    if not entry.related_entry_ids_json or entry.related_entry_ids_json == "[]":
        return

    try:
        amem_related = json.loads(entry.related_entry_ids_json)
    except json.JSONDecodeError:
        return

    for r in amem_related:
        rel = r.get("relation")
        old_id = r.get("related_entry_id")
        if rel in {"equivalent", "conflict"} and old_id:
            old_ke = await db.get(KnowledgeEntry, old_id)
            if old_ke and old_ke.status in {"superseded", "rejected"}:
                old_slug = await resolve_ns_slug(db, old_ke.namespace_id)
                await chroma_delete_safe(old_slug, old_ke)


async def automaton_invalidate_safe(
    db: AsyncSession, namespace_id: int | None, entry_type: str,
) -> None:
    """AC 自动机失效 + 重建 — 仅 terminology 类型触发, 失败仅 log.warning.

    terminology 状态变更 (approve/reject/edit/supersede/delete/conflict) 后调用.
    非 terminology 类型直接跳过 (no-op).
    """
    if entry_type != "terminology":
        return
    try:
        from app.knowledge.terminology_automaton import invalidate, rebuild_all
        await invalidate(namespace_id)
        await rebuild_all(db)
    except Exception as e:
        log.warning("[audit] automaton invalidate 失败 ns_id=%s: %s", namespace_id, e)
