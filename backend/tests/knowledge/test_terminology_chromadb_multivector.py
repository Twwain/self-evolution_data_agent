"""Phase 1b Task 1.4 — terminology canonical 多向量入库 + 状态同步.

契约 (3 测试覆盖):
1. canonical 写入 N+1 向量 (term + 每 synonym 各 1, 共享 entry_id metadata)
2. 同义词查询经 _retrieve_layer3 按 entry_id 去重 → 同 KE 仅返 1 hit
3. canonical → rejected 退离 → ke_{id}_* 全删 (where entry_id 扫净)
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.engine.registry import get_knowledge_collection
from app.knowledge.knowledge_retriever import _retrieve_layer3, upsert_knowledge_entry
from app.models.knowledge_entry import KnowledgeEntry


NS_SLUG = "test_ns"  # 与 seeded_ns_with_mongo_ds fixture 内 hardcode 一致


def _payload_dict(term: str, synonyms: list[str]) -> dict:
    return {
        "term": term,
        "primary_collection": "c_category",
        "primary_database": "db_q",
        "db_type": "mongodb",
        "synonyms": synonyms,
        "source_collections": ["c_category"],
    }


async def _seed_canonical_terminology(
    db, ns_id: int, term: str, synonyms: list[str],
) -> KnowledgeEntry:
    """直接 SQL 种一条 canonical terminology KE (绕过 intake 中间状态)."""
    payload = _payload_dict(term, synonyms)
    ke = KnowledgeEntry(
        namespace_id=ns_id,
        entry_type="terminology",
        source="manual",
        status="canonical",
        tier="normal",
        is_superseded=False,
        payload=json.dumps(payload, ensure_ascii=False),
        content=term,
    )
    db.add(ke)
    await db.commit()
    await db.refresh(ke)
    return ke


@pytest.mark.asyncio
async def test_canonical_writes_n_plus_1_vectors(
    async_session, seeded_ns_with_mongo_ds, real_chromadb,
):
    """ke_{id}_0=term / ke_{id}_1..N=synonyms, 全部共享 entry_id metadata."""
    ns_id, _ = seeded_ns_with_mongo_ds
    async with async_session() as db:
        ke = await _seed_canonical_terminology(db, ns_id, "商品", ["货品", "存货"])

        upsert_knowledge_entry(
            slug=NS_SLUG, entry_id=ke.id, content=ke.content,
            tier="normal", namespace_id=ke.namespace_id,
            entry_type=ke.entry_type, status=ke.status,
            payload=json.loads(ke.payload),
        )

    coll = get_knowledge_collection(NS_SLUG)
    got = coll.get(where={"entry_id": ke.id})
    ids = got.get("ids") or []
    docs = got.get("documents") or []
    metas = got.get("metadatas") or []

    # ── N+1 = 1 (term) + 2 (synonyms) = 3 个向量 ─────────────
    assert len(ids) == 3, f"期望 3 个向量, 实际 {len(ids)}"
    expected_ids = {f"ke_{ke.id}_0", f"ke_{ke.id}_1", f"ke_{ke.id}_2"}
    assert set(ids) == expected_ids

    # ── doc 内容 = term + synonyms ───────────────────────────
    assert set(docs) == {"商品", "货品", "存货"}

    # ── metadata 全部带 entry_id, role 含 1 个 term + 2 个 synonym ─
    assert all(m.get("entry_id") == ke.id for m in metas)
    roles = sorted([str(m.get("role")) for m in metas])
    assert roles == ["synonym", "synonym", "term"]
    assert all(m.get("entry_type") == "terminology" for m in metas)
    assert all(m.get("status") == "canonical" for m in metas)


@pytest.mark.asyncio
async def test_query_synonym_dedups_by_entry_id(
    async_session, seeded_ns_with_mongo_ds, real_chromadb,
):
    """_retrieve_layer3 按 entry_id 去重: 同 KE 多向量召回仅返 1 hit."""
    ns_id, _ = seeded_ns_with_mongo_ds
    async with async_session() as db:
        ke = await _seed_canonical_terminology(db, ns_id, "商品", ["货品", "存货"])
        upsert_knowledge_entry(
            slug=NS_SLUG, entry_id=ke.id, content=ke.content,
            tier="normal", namespace_id=ke.namespace_id,
            entry_type=ke.entry_type, status=ke.status,
            payload=json.loads(ke.payload),
        )

    hits = _retrieve_layer3(NS_SLUG, "存货", entry_types=["terminology"])
    matching = [h for h in hits if h.entry_id == ke.id]
    assert len(matching) == 1, f"同 KE 应仅返 1 hit, 实际 {len(matching)}"
    assert matching[0].entry_type == "terminology"
    assert matching[0].status == "canonical"


@pytest.mark.asyncio
async def test_canonical_to_rejected_deletes_all_vectors(
    async_session, seeded_ns_with_mongo_ds, real_chromadb,
):
    """status canonical → rejected 触发 upsert 时, ke_{id}_* 全删."""
    ns_id, _ = seeded_ns_with_mongo_ds
    async with async_session() as db:
        ke = await _seed_canonical_terminology(db, ns_id, "商品", ["货品", "存货"])
        # canonical 入库
        upsert_knowledge_entry(
            slug=NS_SLUG, entry_id=ke.id, content=ke.content,
            tier="normal", namespace_id=ke.namespace_id,
            entry_type=ke.entry_type, status="canonical",
            payload=json.loads(ke.payload),
        )

    # 确认入库成功
    coll = get_knowledge_collection(NS_SLUG)
    assert len((coll.get(where={"entry_id": ke.id}).get("ids") or [])) == 3

    # 状态退离 → upsert 应触发 delete
    async with async_session() as db:
        loaded = (await db.execute(
            select(KnowledgeEntry).where(KnowledgeEntry.id == ke.id)
        )).scalar_one()
        loaded.status = "rejected"
        await db.commit()
        upsert_knowledge_entry(
            slug=NS_SLUG, entry_id=loaded.id, content=loaded.content,
            tier="normal", namespace_id=loaded.namespace_id,
            entry_type=loaded.entry_type, status="rejected",
            payload=json.loads(loaded.payload),
        )

    got = coll.get(where={"entry_id": ke.id})
    assert (got.get("ids") or []) == [], "rejected 后多向量应全删"
