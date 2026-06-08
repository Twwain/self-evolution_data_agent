"""Spec 2026-05-22 Stage 2 抓手 A — HyQE 多向量测试 (真实 LLM, 不 mock).

契约 (5 测试覆盖):
1. generate_hypothetical_queries 对 rule 返回 1-3 条 ≤50 字查询
2. generate_hypothetical_queries 对不支持类型返回空列表
3. upsert rule → ChromaDB N+1 行 (1 主 + N 假设问题)
4. 多向量召回按 entry_id 去重 (GAP-3 闭合)
5. status canonical → rejected 清空全部 HQ 向量
"""

from __future__ import annotations

import pytest

from app.engine.registry import get_knowledge_collection
from app.knowledge.hypothetical_queries import generate_hypothetical_queries
from app.knowledge.knowledge_retriever import _retrieve_layer3, upsert_knowledge_entry
from app.models.knowledge_entry import KnowledgeEntry

NS_SLUG = "test_ns"  # 与 seeded_ns_with_mongo_ds fixture 内 hardcode 一致


# ════════════════════════════════════════════
#  同步测试 — generate_hypothetical_queries (真实 LLM)
# ════════════════════════════════════════════


def test_generate_returns_n_queries_for_rule():
    """rule 类型应返回 1-3 条假设触发问题, 每条 ≤50 字."""
    queries = generate_hypothetical_queries(
        content="只统计已支付订单",
        entry_type="rule",
        n=3,
    )
    assert isinstance(queries, list)
    assert 1 <= len(queries) <= 3, f"应返 1-3 条, 实际 {len(queries)}"
    for q in queries:
        assert isinstance(q, str) and 0 < len(q) <= 50


def test_generate_returns_empty_on_unsupported_type():
    """不支持的 entry_type (terminology) 应直接返回空列表."""
    queries = generate_hypothetical_queries(
        content="some terminology",
        entry_type="terminology",
        n=3,
    )
    assert queries == []


# ════════════════════════════════════════════
#  异步测试 — ChromaDB N+1 行 / 去重 / 删除
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_upsert_rule_creates_n_plus_1_chroma_rows(
    async_session, seeded_ns_with_mongo_ds, real_chromadb,
):
    """canonical rule upsert → ChromaDB 写 N+1 行 (ke_{id} 主 + ke_{id}_hq_* 多向量)."""
    ns_id, _ = seeded_ns_with_mongo_ds
    async with async_session() as db:
        ke = KnowledgeEntry(
            namespace_id=ns_id,
            entry_type="rule",
            content="只统计已支付订单",
            source="manual",
            status="canonical",
            tier="normal",
        )
        db.add(ke)
        await db.commit()
        await db.refresh(ke)

        upsert_knowledge_entry(
            slug=NS_SLUG,
            entry_id=ke.id,
            content=ke.content,
            tier="normal",
            namespace_id=ke.namespace_id,
            entry_type="rule",
            status="canonical",
        )

    coll = get_knowledge_collection(NS_SLUG)
    res = coll.get(where={"entry_id": ke.id})
    ids = res["ids"]

    # 主向量 ke_{id} 必须存在
    assert any(i == f"ke_{ke.id}" for i in ids), f"主向量 ke_{ke.id} 缺失"
    # 假设问题向量 ke_{id}_hq_*
    hq_ids = [i for i in ids if i.startswith(f"ke_{ke.id}_hq_")]
    assert 1 <= len(hq_ids) <= 3, f"HQ 向量应 1-3 条, 实际 {len(hq_ids)}"
    # 总行数 = 1 主 + N 假设
    assert len(ids) == 1 + len(hq_ids)


@pytest.mark.asyncio
async def test_dedup_by_entry_id_in_retrieve(
    async_session, seeded_ns_with_mongo_ds, real_chromadb,
):
    """GAP-3 闭合: 多向量召回多条 hit 但 entry_id 相同, 去重后只剩一条."""
    ns_id, _ = seeded_ns_with_mongo_ds
    async with async_session() as db:
        ke = KnowledgeEntry(
            namespace_id=ns_id,
            entry_type="rule",
            content="活跃用户指 30 天内有过登录的用户",
            source="manual",
            status="canonical",
            tier="normal",
        )
        db.add(ke)
        await db.commit()
        await db.refresh(ke)

        upsert_knowledge_entry(
            slug=NS_SLUG,
            entry_id=ke.id,
            content=ke.content,
            tier="normal",
            namespace_id=ke.namespace_id,
            entry_type="rule",
            status="canonical",
        )

    hits = _retrieve_layer3(
        NS_SLUG,
        "本月活跃用户数",
        entry_types=["rule"],
        k_normal=10,
    )
    same_entry = [h for h in hits if h.entry_id == ke.id]
    assert len(same_entry) <= 1, "同 entry_id 多向量召回应去重为 1"


@pytest.mark.asyncio
async def test_status_canonical_to_rejected_clears_hq_vectors(
    async_session, seeded_ns_with_mongo_ds, real_chromadb,
):
    """status canonical → rejected 时, 全部 HQ 向量应从 ChromaDB 清空."""
    ns_id, _ = seeded_ns_with_mongo_ds
    async with async_session() as db:
        ke = KnowledgeEntry(
            namespace_id=ns_id,
            entry_type="rule",
            content="只统计已支付订单",
            source="manual",
            status="canonical",
            tier="normal",
        )
        db.add(ke)
        await db.commit()
        await db.refresh(ke)

        # canonical 入库
        upsert_knowledge_entry(
            slug=NS_SLUG,
            entry_id=ke.id,
            content=ke.content,
            tier="normal",
            namespace_id=ke.namespace_id,
            entry_type="rule",
            status="canonical",
        )

    # 确认入库成功
    coll = get_knowledge_collection(NS_SLUG)
    pre_ids = coll.get(where={"entry_id": ke.id})["ids"]
    assert len(pre_ids) >= 2, f"入库后应有 ≥2 行, 实际 {len(pre_ids)}"

    # 状态退离 → rejected
    upsert_knowledge_entry(
        slug=NS_SLUG,
        entry_id=ke.id,
        content=ke.content,
        tier="normal",
        namespace_id=ke.namespace_id,
        entry_type="rule",
        status="rejected",
    )

    got = coll.get(where={"entry_id": ke.id})
    assert got["ids"] == [], "rejected 后 HQ 多向量应全删"
