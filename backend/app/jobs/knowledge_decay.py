"""Stage 2 抓手 B — 知识新陈代谢: 采纳率低 + 长期未召回 → status=superseded.

与 BulkOperationGuard 宪章的关系 (D8 显式宣告):
- decay sweep 不走 BulkOperationGuard — sweep 是软删/状态转换 (canonical → superseded),
  不是真删, 套 guard 反而扭曲语义.
- 手动实现宪章对应保护:
  §1 dry_run 模式: sweep_once(dry_run=True) 仅扫描不写
  §3 人类编辑兜底: 跳过 audit_log 中 actor_id != NULL ∧ action ∈ {approve, edit} 的条目
  §4 必写 audit_log: 每条转换写 KnowledgeAuditLog action='expire' actor_id=NULL
  §5 影响数报告: 返 dict 含 rule1 / rule2 / decayed / preserved_audited 计数
  §6 ChromaDB 同步: 状态转 superseded 后 delete_knowledge_entry 清向量

CLI: python -m app.jobs.knowledge_decay
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import settings
from app.knowledge.audit import write_audit
from app.knowledge.knowledge_retriever import delete_knowledge_entry
from app.models import KnowledgeAuditLog, KnowledgeEntry, Namespace

log = logging.getLogger(__name__)


async def sweep_once(
    dry_run: bool = False,
    session_factory=None,
) -> dict:
    """单次扫描 — 返报告 dict. dry_run=True 时仅 SELECT 不 UPDATE (D8 §1).

    session_factory: 可选, 默认用 app.db.metadata.async_session. 测试注入用.
    """
    if session_factory is None:
        from app.db.metadata import async_session
        session_factory = async_session

    async with session_factory() as db:
        # namespace slug 映射 (ChromaDB delete 需要 slug)
        ns_map: dict[int | None, str] = {None: "__global__"}
        for ns in (await db.execute(select(Namespace))).scalars().all():
            ns_map[ns.id] = ns.slug

        # 规则 1: 采纳率低 — recall_count > threshold AND adopted/recall < ratio
        stmt1 = select(KnowledgeEntry).where(
            KnowledgeEntry.status == "canonical",
            KnowledgeEntry.recall_count > settings.kb_decay_recall_threshold,
        )
        rule1_all = (await db.execute(stmt1)).scalars().all()
        rule1 = [
            ke for ke in rule1_all
            if ke.recall_count > 0
            and (ke.adopted_count / ke.recall_count) < settings.kb_decay_adoption_ratio
        ]

        # 规则 2: 长期未召回 — last_recalled_at 早于 stale_days
        threshold_dt = datetime.now() - timedelta(days=settings.kb_decay_stale_days)
        stmt2 = select(KnowledgeEntry).where(
            KnowledgeEntry.status == "canonical",
            KnowledgeEntry.last_recalled_at < threshold_dt,
        )
        rule2 = (await db.execute(stmt2)).scalars().all()

        # 合并 (按 id 去重)
        targets: dict[int, KnowledgeEntry] = {ke.id: ke for ke in rule1}
        for ke in rule2:
            targets.setdefault(ke.id, ke)

        # D8 §3 人类编辑兜底: 跳过 audit_log actor_id != NULL ∧ action ∈ {approve, edit}
        preserved_ids: set[int] = set()
        if targets:
            audited = (await db.execute(
                select(KnowledgeAuditLog.entry_id).where(
                    KnowledgeAuditLog.entry_id.in_(list(targets.keys())),
                    KnowledgeAuditLog.actor_id.isnot(None),
                    KnowledgeAuditLog.action.in_(["approve", "edit"]),
                ).distinct()
            )).scalars().all()
            preserved_ids = {eid for eid in audited if eid is not None}

        sweep_targets = {
            kid: ke for kid, ke in targets.items() if kid not in preserved_ids
        }

        log.info(
            "decay sweep: rule1=%d rule2=%d total=%d preserved=%d sweep=%d dry_run=%s",
            len(rule1), len(rule2), len(targets),
            len(preserved_ids), len(sweep_targets), dry_run,
        )

        if dry_run:
            return {
                "rule1": len(rule1),
                "rule2": len(rule2),
                "decayed": 0,
                "would_decay": len(sweep_targets),
                "preserved_audited": len(preserved_ids),
            }

        decayed = 0
        for ke in sweep_targets.values():
            slug = ns_map.get(ke.namespace_id, "__global__")
            ke.status = "superseded"
            ke.is_superseded = True
            await write_audit(
                db,
                entry_id=ke.id,
                actor_id=None,
                action="expire",
                from_status="canonical",
                to_status="superseded",
                reason=(
                    f"knowledge decay sweep: recall={ke.recall_count} "
                    f"adopted={ke.adopted_count} last_recalled={ke.last_recalled_at}"
                ),
                diff={},
            )
            try:
                delete_knowledge_entry(
                    slug=slug,
                    entry_id=ke.id,
                    namespace_id=ke.namespace_id,
                    entry_type=ke.entry_type,
                )
            except Exception as e:
                log.warning("[decay] chroma delete fail ke=%d: %s", ke.id, e)
            decayed += 1
        await db.commit()
        return {
            "rule1": len(rule1),
            "rule2": len(rule2),
            "decayed": decayed,
            "preserved_audited": len(preserved_ids),
        }


async def decay_loop() -> None:
    """FastAPI lifespan 注册的后台循环 (复用 auto_expire 异常隔离模式)."""
    interval_secs = settings.kb_decay_check_interval_hours * 3600
    log.info("[decay_loop] 启动知识衰减循环 interval=%ds", interval_secs)

    while True:
        try:
            report = await sweep_once()
            log.info("[decay_loop] %s", report)
        except asyncio.CancelledError:
            log.info("[decay_loop] 收到 cancel, 退出循环")
            raise
        except Exception as e:
            log.error("[decay_loop] sweep failed: %s", e, exc_info=True)

        try:
            await asyncio.sleep(interval_secs)
        except asyncio.CancelledError:
            log.info("[decay_loop] sleep 被 cancel, 退出循环")
            raise


def main() -> int:
    """CLI 入口: python -m app.jobs.knowledge_decay"""
    logging.basicConfig(level=logging.INFO)
    return asyncio.run(_main())


async def _main() -> int:
    report = await sweep_once()
    print(f"sweep report: {report}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
