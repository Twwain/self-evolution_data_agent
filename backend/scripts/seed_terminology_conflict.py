#!/usr/bin/env python3
"""seed_terminology_conflict.py — e2e 测试专用 seed: 造一对 (canonical KE + open conflict).

# ════════════════════════════════════════════
#  用途
# ════════════════════════════════════════════
# 给 playwright e2e (frontend/e2e/terminology_conflict.spec.ts) 准备真实数据.
# 步骤:
#   1. ensure namespace (slug=e2e_terminology, db_type=mongodb)
#   2. ensure datasource (database=e2e_db, db_type=mongodb)
#   3. upsert canonical KnowledgeEntry(entry_type=terminology, status=canonical,
#      source=manual, payload={term='商品', synonyms=['货品'], primary_collection='c_categories',
#      primary_database='e2e_db', db_type='mongodb'})
#   4. trigger _record_conflict via upsert_terminology_with_validation with
#      git source candidate (term='条目' synonyms=['明细']) - lex 不重叠 → 触发 conflict
#   5. 输出 ns_id / conflict_id 给 e2e 消费 (stdout JSON 或 --json flag)
#
# 幂等: 重复跑会清空老的 e2e ns 全量数据后再造 (--reset 默认 True).
# 安全: 仅限 slug=e2e_terminology 的 namespace, 防误删生产数据.

# ════════════════════════════════════════════
#  使用
# ════════════════════════════════════════════
#   cd backend && python scripts/seed_terminology_conflict.py
#   cd backend && python scripts/seed_terminology_conflict.py --json     # 仅输出 JSON 给 e2e
#   cd backend && python scripts/seed_terminology_conflict.py --cleanup  # 仅清理, 不 seed
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from app.db.metadata import async_session
from app.knowledge.terminology_intake import upsert_terminology_with_validation
from app.models import KnowledgeEntry, Namespace
from app.models.namespace import DataSource
from app.models.terminology_conflict import TerminologyConflict


E2E_NS_SLUG = "e2e_terminology"
E2E_NS_NAME = "e2e 术语冲突测试空间"
E2E_DB_NAME = "e2e_db"
E2E_COLLECTION = "c_categories"


async def _ensure_namespace(db) -> Namespace:
    ns = (await db.execute(
        select(Namespace).where(Namespace.slug == E2E_NS_SLUG)
    )).scalar_one_or_none()
    if ns is not None:
        return ns
    ns = Namespace(slug=E2E_NS_SLUG, name=E2E_NS_NAME, description="e2e seed")
    db.add(ns)
    await db.flush()
    return ns


async def _ensure_datasource(db, ns_id: int) -> DataSource:
    ds = (await db.execute(
        select(DataSource).where(
            DataSource.namespace_id == ns_id,
            DataSource.database == E2E_DB_NAME,
        )
    )).scalar_one_or_none()
    if ds is not None:
        return ds
    ds = DataSource(
        namespace_id=ns_id,
        db_type="mongodb",
        host="localhost",
        port=27017,
        database=E2E_DB_NAME,
        username="",
        password="",
    )
    db.add(ds)
    await db.flush()
    return ds


async def _cleanup_ns(db, ns_id: int) -> dict:
    """清空该 ns 的 terminology 数据 (KE + conflict) — 仅限 e2e ns, 安全."""
    conf_n = (await db.execute(
        delete(TerminologyConflict).where(TerminologyConflict.namespace_id == ns_id)
    )).rowcount
    ke_n = (await db.execute(
        delete(KnowledgeEntry).where(
            KnowledgeEntry.namespace_id == ns_id,
            KnowledgeEntry.entry_type == "terminology",
        )
    )).rowcount
    return {"conflicts_deleted": conf_n, "kes_deleted": ke_n}


async def _seed_canonical(db, ns_id: int) -> KnowledgeEntry:
    """直插一条 canonical (不走闸门, 因为闸门会写 proposed)."""
    payload = json.dumps({
        "term": "商品",
        "synonyms": ["货品"],
        "primary_collection": E2E_COLLECTION,
        "primary_database": E2E_DB_NAME,
        "db_type": "mongodb",
        "source_collections": [E2E_COLLECTION],
    }, ensure_ascii=False)
    ke = KnowledgeEntry(
        namespace_id=ns_id,
        entry_type="terminology",
        source="manual",
        status="canonical",
        is_superseded=False,
        payload=payload,
        content="商品",
        raw_input="e2e seed",
        evidence_json="{}",
    )
    db.add(ke)
    await db.flush()
    return ke


async def _trigger_conflict(db, ns_id: int) -> TerminologyConflict | None:
    """用 git source 投个 lex 完全不重叠的 candidate, 走闸门触发 _record_conflict."""
    candidate_payload = {
        "term": "条目",
        "synonyms": ["明细"],
        "primary_collection": E2E_COLLECTION,
        "primary_database": E2E_DB_NAME,
        "db_type": "mongodb",
        "source_collections": [E2E_COLLECTION],
    }
    result = await upsert_terminology_with_validation(
        db, ns_id=ns_id, payload_dict=candidate_payload,
        source="git", repo_id=None,
    )
    if result is not None:
        # 闸门没识别为冲突 (理论上不该走到), 直接报错
        raise RuntimeError(
            f"expected conflict but got KE id={result.id} status={result.status}",
        )
    conflict = (await db.execute(
        select(TerminologyConflict).where(
            TerminologyConflict.namespace_id == ns_id,
            TerminologyConflict.status == "open",
        ).order_by(TerminologyConflict.id.desc())
    )).scalar_one_or_none()
    return conflict


async def seed(cleanup_only: bool = False) -> dict:
    async with async_session() as db:
        ns = await _ensure_namespace(db)
        await _ensure_datasource(db, ns.id)
        cleanup = await _cleanup_ns(db, ns.id)
        if cleanup_only:
            await db.commit()
            return {"namespace_id": ns.id, "namespace_slug": ns.slug, "cleanup": cleanup}

        canonical = await _seed_canonical(db, ns.id)
        conflict = await _trigger_conflict(db, ns.id)
        await db.commit()

        if conflict is None:
            raise RuntimeError("conflict 触发失败 — 检查 _record_conflict 路径")

        return {
            "namespace_id": ns.id,
            "namespace_slug": ns.slug,
            "canonical_entry_id": canonical.id,
            "conflict_id": conflict.id,
            "cleanup_before_seed": cleanup,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="只输出 JSON, 静默其他日志")
    parser.add_argument("--cleanup", action="store_true", help="仅清理 e2e ns, 不 seed")
    args = parser.parse_args()

    result = asyncio.run(seed(cleanup_only=args.cleanup))
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"[seed_terminology_conflict] OK: {json.dumps(result, ensure_ascii=False, indent=2)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
