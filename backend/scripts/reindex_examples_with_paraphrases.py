#!/usr/bin/env python3
"""
reindex_examples_with_paraphrases.py — 一次性回灌 example 向量索引

将所有 status=canonical 的 example KnowledgeEntry 重新索引到 ChromaDB,
使用 build_example_content(payload) 拼接 question + nl_paraphrases 作为索引内容.

幂等: ChromaDB upsert — 已存在的 entry 会被覆盖更新, 可重复运行.

使用方式:
    cd backend
    python -m scripts.reindex_examples_with_paraphrases
    python -m scripts.reindex_examples_with_paraphrases --dry-run
    python -m scripts.reindex_examples_with_paraphrases --ns demo
"""

import asyncio
import json
import sys
from pathlib import Path

# ── 项目根路径注入 ──────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.metadata import async_session
from app.knowledge.knowledge_content import build_example_content
from app.knowledge.knowledge_retriever import upsert_knowledge_entry
from app.models import KnowledgeEntry, Namespace


async def reindex(dry_run: bool = False, ns_slug_filter: str | None = None) -> dict:
    """
    遍历所有 status=canonical, entry_type=example 的 KnowledgeEntry,
    用 build_example_content 重建索引内容后 upsert 到 ChromaDB.

    返回统计: {total, upserted, skipped, errors}

    Design choice: 直接调用 upsert_knowledge_entry 写 ChromaDB, 而非仅更新
    KnowledgeEntry.content 后依赖 cron 同步. 原因:
    - 当前 KnowledgeEntry 无 chromadb_dirty 标记列, 无法标记需重新同步的行
    - 直接 upsert 是幂等的 (ChromaDB upsert 语义), 可重复运行
    - 未来若引入 dirty 标记, 可改为仅更新 SQLite + 依赖 cron 批量同步
    """
    stats = {"total": 0, "upserted": 0, "skipped": 0, "errors": 0}

    async with async_session() as db:
        ns_rows = (await db.execute(select(Namespace))).scalars().all()
        ns_map: dict[int, str] = {ns.id: ns.slug for ns in ns_rows}

        query = select(KnowledgeEntry).where(
            KnowledgeEntry.entry_type == "example",
            KnowledgeEntry.status == "canonical",
        )

        if ns_slug_filter:
            target_ns_ids = [
                ns_id for ns_id, slug in ns_map.items() if slug == ns_slug_filter
            ]
            if not target_ns_ids:
                print(f"[warn] 未找到 namespace slug={ns_slug_filter!r}")
            from sqlalchemy import or_
            query = query.where(or_(
                KnowledgeEntry.namespace_id.in_(target_ns_ids),
                KnowledgeEntry.namespace_id.is_(None),
            ))

        entries = (await db.execute(query)).scalars().all()
        stats["total"] = len(entries)
        print(f"[reindex] 共 {len(entries)} 条 example KnowledgeEntry 待处理")

        for entry in entries:
            if entry.namespace_id is None:
                slug = ""
            else:
                slug = ns_map.get(entry.namespace_id, "")
                if not slug:
                    print(f"  [skip] entry_id={entry.id} namespace_id={entry.namespace_id} 无对应 namespace")
                    stats["skipped"] += 1
                    continue

            payload = _safe_parse_payload(entry.payload)
            # 用 build_example_content 构建索引内容
            if payload:
                index_content = build_example_content(payload)
            else:
                index_content = entry.content

            if dry_run:
                has_paraphrases = bool(payload and payload.get("nl_paraphrases"))
                print(
                    f"  [dry-run] entry_id={entry.id} slug={slug or '__global__'} "
                    f"has_paraphrases={has_paraphrases}"
                )
                stats["upserted"] += 1
                continue

            try:
                upsert_knowledge_entry(
                    slug=slug,
                    entry_id=entry.id,
                    content=index_content,
                    tier=entry.tier,
                    namespace_id=entry.namespace_id,
                    entry_type=entry.entry_type,
                    status=entry.status,
                    payload=payload,
                )
                stats["upserted"] += 1
                if stats["upserted"] % 50 == 0:
                    print(f"  [progress] {stats['upserted']}/{stats['total']}")
            except Exception as exc:
                print(f"  [error] entry_id={entry.id}: {exc}")
                stats["errors"] += 1

    return stats


def _safe_parse_payload(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        return None


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="回灌 example KnowledgeEntry 向量索引 (question + nl_paraphrases)"
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计, 不写入 ChromaDB")
    parser.add_argument("--ns", metavar="SLUG", help="仅处理指定 namespace slug")
    args = parser.parse_args()

    stats = asyncio.run(reindex(dry_run=args.dry_run, ns_slug_filter=args.ns))

    print("\n" + "=" * 40)
    print(
        f"回灌完成: total={stats['total']} upserted={stats['upserted']} "
        f"skipped={stats['skipped']} errors={stats['errors']}"
    )
    if stats["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
