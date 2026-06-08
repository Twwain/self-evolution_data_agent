#!/usr/bin/env python3
"""一次性回填: 历史 canonical rule / route_hint 条目重新走 HyQE 多向量上池.

幂等: ChromaDB upsert 覆盖, 重跑安全.

使用方式:
    cd backend
    python -m scripts.backfill_hypothetical_queries              # dry-run (默认)
    python -m scripts.backfill_hypothetical_queries --execute    # 真执行
    python -m scripts.backfill_hypothetical_queries --execute --ns demo  # 仅指定 namespace
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# ── 项目根路径注入 ──────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.metadata import async_session
from app.knowledge.knowledge_retriever import upsert_knowledge_entry
from app.models import KnowledgeEntry, Namespace

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("backfill_hypothetical")


async def _amain(dry_run: bool, ns_slug: str | None) -> int:
    stats = {"total": 0, "processed": 0, "skipped": 0, "errors": 0}

    async with async_session() as db:
        # ── 加载 namespace 映射 ──
        ns_rows = (await db.execute(select(Namespace))).scalars().all()
        ns_map: dict[int | None, str] = {None: "__global__"}
        for ns in ns_rows:
            ns_map[ns.id] = ns.slug

        # ── 查询目标条目: canonical rule / route_hint, 非 critical ──
        stmt = select(KnowledgeEntry).where(
            KnowledgeEntry.entry_type.in_(["rule", "route_hint"]),
            KnowledgeEntry.status == "canonical",
            KnowledgeEntry.tier != "critical",
        )
        rows = (await db.execute(stmt)).scalars().all()

        # ── 按 namespace 过滤 ──
        target = [r for r in rows if (
            ns_slug is None or ns_map.get(r.namespace_id) == ns_slug
        )]
        stats["total"] = len(target)
        log.info("target=%d dry_run=%s ns_filter=%s", len(target), dry_run, ns_slug)

        for ke in target:
            slug = ns_map.get(ke.namespace_id, "__global__")
            if dry_run:
                log.info("[dry-run] ke=%d type=%s ns=%s content=%.40s",
                         ke.id, ke.entry_type, slug, ke.content)
                stats["processed"] += 1
                continue

            try:
                await asyncio.to_thread(
                    upsert_knowledge_entry,
                    slug=slug, entry_id=ke.id, content=ke.content,
                    tier=ke.tier, namespace_id=ke.namespace_id,
                    entry_type=ke.entry_type, status=ke.status,
                )
                stats["processed"] += 1
                if stats["processed"] % 10 == 0:
                    log.info("[progress] %d/%d", stats["processed"], stats["total"])
            except Exception as e:
                log.error("[error] ke=%d: %s", ke.id, e)
                stats["errors"] += 1

    log.info("完成: total=%d processed=%d skipped=%d errors=%d",
             stats["total"], stats["processed"], stats["skipped"], stats["errors"])
    return 1 if stats["errors"] else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="回填历史 canonical rule/route_hint 的 HyQE 多向量")
    ap.add_argument("--execute", action="store_true", help="真执行 (默认 dry-run)")
    ap.add_argument("--ns", default=None, metavar="SLUG", help="仅处理指定 namespace slug")
    args = ap.parse_args()
    return asyncio.run(_amain(dry_run=not args.execute, ns_slug=args.ns))


if __name__ == "__main__":
    sys.exit(main())
