"""一次性归档 schema_summary 条目 (Spec 2026-05-22-knowledge-pipeline-rebuild Stage 1 GAP-2).

走 BulkOperationGuard 宪章 6 条:
  scope_filter=entry_type=['schema_summary']
  dry_run=False
  actor_id 从 IS_ARCHIVE_ACTOR_ID env (或 users 表首个 admin fallback)
  reason="spec 2026-05-22 schema_summary lifecycle deprecated"

执行前必须先 dry_run 看影响数, 由调用方人工 review 后改 --execute 真跑.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from sqlalchemy import distinct, select

from app.db.metadata import async_session
from app.knowledge.bulk_guard import BulkOperationGuard
from app.models import KnowledgeEntry, Namespace
from app.models.user import User

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("archive_schema_summary")


async def _resolve_actor_id() -> int:
    env_id = os.environ.get("IS_ARCHIVE_ACTOR_ID")
    if env_id:
        return int(env_id)
    # fallback: users 表首个 role='admin'
    async with async_session() as db:
        row = (await db.execute(
            select(User).where(User.role == "admin").order_by(User.id).limit(1)
        )).scalar_one_or_none()
        if row is None:
            raise RuntimeError("无 admin 用户可用作 actor_id, 请设置 IS_ARCHIVE_ACTOR_ID")
        return row.id


async def _amain(execute: bool) -> int:
    actor_id = await _resolve_actor_id()
    log.info("actor_id=%d execute=%s", actor_id, execute)

    async with async_session() as db:
        # 收集所有命中 schema_summary 的 namespace_id (含 NULL 全局)
        ns_ids = (await db.execute(
            select(distinct(KnowledgeEntry.namespace_id)).where(
                KnowledgeEntry.entry_type == "schema_summary"
            )
        )).scalars().all()

        if not ns_ids:
            log.info("无 schema_summary 条目, 无需清理")
            return 0

        ns_map: dict[int | None, str] = {None: "__global__"}
        for ns in (await db.execute(select(Namespace))).scalars().all():
            ns_map[ns.id] = ns.slug

        total = {"affected": 0, "preserved": 0, "chroma_failed": 0}
        for ns_id in ns_ids:
            slug = ns_map.get(ns_id, "__global__")
            scope: dict = {"entry_type": ["schema_summary"]}
            if ns_id is not None:
                scope["namespace_id"] = ns_id

            # 全局 (ns_id IS NULL) — BulkOpGuard 不支持 IS NULL 过滤,
            # 但不传 namespace_id 则不按 ns 过滤, 会命中所有 ns.
            # 对全局条目: 单独查出 id 列表后逐条删.
            if ns_id is None:
                await _archive_global_entries(db, actor_id, execute)
                continue

            guard = BulkOperationGuard(
                op_name=f"stage1_schema_summary_archive_ns_{ns_id}",
                scope_filter=scope,  # type: ignore[arg-type]
                dry_run=not execute,
                actor_id=actor_id,
                reason="spec 2026-05-22 schema_summary lifecycle deprecated",
            )
            report = await guard.execute(db, slug=slug)
            log.info(
                "ns=%s (slug=%s): affected=%d preserved=%d chroma_deleted=%d failed=%s",
                ns_id, slug, report.affected_count,
                report.preserved_audited_count,
                report.chromadb_deleted_count, report.chromadb_failed_ids,
            )
            total["affected"] += report.affected_count
            total["preserved"] += report.preserved_audited_count
            total["chroma_failed"] += len(report.chromadb_failed_ids)

        log.info("TOTAL: %s", total)
    return 0


async def _archive_global_entries(db, actor_id: int, execute: bool) -> None:
    """全局 (namespace_id IS NULL) 条目单独处理."""
    from app.knowledge.knowledge_retriever import delete_knowledge_entry

    rows = (await db.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.entry_type == "schema_summary",
            KnowledgeEntry.namespace_id.is_(None),
        )
    )).scalars().all()
    log.info("global (ns IS NULL): %d entries", len(rows))
    if not execute or not rows:
        return

    deleted_ids: list[int] = []
    for ke in rows:
        try:
            delete_knowledge_entry(
                slug="__global__", entry_id=ke.id,
                namespace_id=None, entry_type=ke.entry_type,
            )
        except Exception as e:
            log.warning("[archive] global chroma del fail ke=%d: %s", ke.id, e)
        await db.delete(ke)
        deleted_ids.append(ke.id)

    if deleted_ids:
        from app.knowledge.audit import write_audit
        await write_audit(
            db, entry_id=deleted_ids[0], action="bulk_delete",
            from_status="any", to_status="deleted",
            actor_id=actor_id,
            reason="spec 2026-05-22 schema_summary lifecycle deprecated (global)",
            diff={"deleted_ids": deleted_ids, "scope": "namespace_id IS NULL"},
        )
    await db.commit()
    log.info("global: deleted %d entries", len(deleted_ids))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="实际删除 (默认 dry_run)")
    args = ap.parse_args()
    return asyncio.run(_amain(execute=args.execute))


if __name__ == "__main__":
    sys.exit(main())
