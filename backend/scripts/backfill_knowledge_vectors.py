#!/usr/bin/env python3
"""
backfill_knowledge_vectors.py — 历史 KnowledgeEntry 回填向量索引

将数据库中所有未被 superseded 的 KnowledgeEntry 写入 ns_{slug}_knowledge ChromaDB 集合.
幂等: ChromaDB upsert — 已存在的 entry 会被覆盖更新, 可重复运行.

使用方式:
    cd backend
    python scripts/backfill_knowledge_vectors.py
    python scripts/backfill_knowledge_vectors.py --dry-run   # 只统计，不写入
    python scripts/backfill_knowledge_vectors.py --ns demo   # 只处理指定 namespace slug
"""

import asyncio
import sys
from pathlib import Path

# ── 项目根路径注入 ──────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.db.metadata import async_session
from app.knowledge.knowledge_retriever import upsert_knowledge_entry
from app.models import KnowledgeEntry, Namespace


# ════════════════════════════════════════════
#  回填核心
# ════════════════════════════════════════════

async def backfill(dry_run: bool = False, ns_slug_filter: str | None = None) -> dict:
    """
    遍历所有未 superseded 的 KnowledgeEntry, 写入向量集合.

    返回统计: {total, upserted, skipped, errors}
    """
    stats = {"total": 0, "upserted": 0, "skipped": 0, "errors": 0}

    async with async_session() as db:
        # ── 加载所有 namespace (为了拿 slug) ──
        ns_rows = (await db.execute(select(Namespace))).scalars().all()
        ns_map: dict[int, str] = {ns.id: ns.slug for ns in ns_rows}

        if ns_slug_filter:
            # 按 slug 过滤 namespace_id，同时保留全局条目 (namespace_id IS NULL)
            target_ns_ids = {ns_id for ns_id, slug in ns_map.items() if slug == ns_slug_filter}
            if not target_ns_ids:
                print(f"[warn] 未找到 namespace slug={ns_slug_filter!r}")
            filter_cond = or_(
                KnowledgeEntry.namespace_id.in_(target_ns_ids),
                KnowledgeEntry.namespace_id.is_(None),
            )
        else:
            filter_cond = KnowledgeEntry.is_superseded.is_(False)

        # ── 查询目标条目 ──
        query = select(KnowledgeEntry).where(
            KnowledgeEntry.is_superseded.is_(False),
        )
        if ns_slug_filter:
            query = query.where(filter_cond)

        entries = (await db.execute(query)).scalars().all()
        stats["total"] = len(entries)

        print(f"[backfill] 共 {len(entries)} 条 KnowledgeEntry 待处理")

        for entry in entries:
            # 全局知识 (namespace_id=None) 用空 slug
            if entry.namespace_id is None:
                slug = ""
            else:
                slug = ns_map.get(entry.namespace_id, "")
                if not slug:
                    print(f"  [skip] entry_id={entry.id} namespace_id={entry.namespace_id} 无对应 namespace")
                    stats["skipped"] += 1
                    continue

            if dry_run:
                print(f"  [dry-run] entry_id={entry.id} slug={slug or '__global__'} tier={entry.tier} type={entry.entry_type}")
                stats["upserted"] += 1
                continue

            try:
                upsert_knowledge_entry(
                    slug=slug,
                    entry_id=entry.id,
                    content=entry.content,
                    tier=entry.tier,
                    namespace_id=entry.namespace_id,
                    entry_type=entry.entry_type,
                    status=entry.status,
                    payload=_safe_parse_payload(entry.payload),
                )
                stats["upserted"] += 1
                if stats["upserted"] % 50 == 0:
                    print(f"  [progress] {stats['upserted']}/{stats['total']}")
            except Exception as exc:
                print(f"  [error] entry_id={entry.id}: {exc}")
                stats["errors"] += 1

    return stats


def _safe_parse_payload(raw: str | None) -> dict | None:
    """KnowledgeEntry.payload 是 JSON Text — terminology 多向量入库需要 dict.

    解析失败 / 空字符串 / 非对象返 None, 上层 upsert 静默跳过 (脏数据保护).
    """
    import json
    if not raw:
        return None
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        return None


# ════════════════════════════════════════════
#  CLI 入口
# ════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="回填 KnowledgeEntry 向量索引")
    parser.add_argument("--dry-run", action="store_true", help="只统计, 不写入 ChromaDB")
    parser.add_argument("--ns", metavar="SLUG", help="仅处理指定 namespace slug")
    args = parser.parse_args()

    stats = asyncio.run(backfill(dry_run=args.dry_run, ns_slug_filter=args.ns))

    print("\n" + "=" * 40)
    print(f"回填完成: total={stats['total']} upserted={stats['upserted']} "
          f"skipped={stats['skipped']} errors={stats['errors']}")
    if stats["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
