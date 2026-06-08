"""Phase 5 Task 5.3 — load_all_knowledge span_io telemetry contract.

验证 record_span_io 被调用时携带 5 个必需 output keys:
    critical_count / term_count / route_hint_count
    / term_hit_ids / route_hint_hit_ids
以及 name="load_all_knowledge" 与 question/ns_slug input.

真实 SQLite + ChromaDB (按 §06 规则不 mock 业务路径).
仅 record_span_io 自身被 monkeypatch 截获 (langfuse trace 捕获器).
"""

import json

import pytest
import pytest_asyncio

from app.knowledge.knowledge_loader import load_all_knowledge
from app.knowledge.knowledge_retriever import upsert_knowledge_entry
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import Namespace


# ════════════════════════════════════════════
#  fixture (hoist 自 test_knowledge_loader.py:seeded_full_bundle)
# ════════════════════════════════════════════

@pytest_asyncio.fixture
async def seeded_full_bundle(
    async_session, real_chromadb,
) -> tuple[int, str]:
    """ns + 1 critical rule + 1 terminology vectorized + 1 route_hint vectorized."""
    async with async_session() as db:
        ns = Namespace(name="span_ns", slug="span_ns", description="phase5-span")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        crit = KnowledgeEntry(
            namespace_id=ns.id,
            entry_type="rule",
            status="canonical",
            tier="critical",
            content="critical-rule: 按 namespace 隔离",
            source="manual",
        )
        db.add(crit)

        t_payload = {
            "term": "商品",
            "primary_collection": "category",
            "primary_database": "db_q",
            "db_type": "mongodb",
            "synonyms": ["货品"],
            "source_collections": ["category"],
        }
        term_ke = KnowledgeEntry(
            namespace_id=ns.id,
            entry_type="terminology",
            status="canonical",
            tier="normal",
            content="商品是 category 集合的别名",
            source="manual",
            payload=json.dumps(t_payload),
        )
        db.add(term_ke)

        rh_payload = {
            "question_pattern": "商品统计",
            "collection_path": ["category"],
            "join_fields": [],
            "avoid_path": [],
            "cost_strategy": "default",
            "reason": "商品类查询直走 category",
        }
        rh_ke = KnowledgeEntry(
            namespace_id=ns.id,
            entry_type="route_hint",
            status="canonical",
            tier="normal",
            content="商品类问题路由 category",
            source="manual",
            payload=json.dumps(rh_payload),
        )
        db.add(rh_ke)
        await db.commit()
        await db.refresh(term_ke)
        await db.refresh(rh_ke)

        upsert_knowledge_entry(
            slug=ns.slug, entry_id=term_ke.id, content=term_ke.content,
            tier="normal", namespace_id=ns.id, entry_type="terminology",
            status="canonical", payload=t_payload,
        )
        upsert_knowledge_entry(
            slug=ns.slug, entry_id=rh_ke.id, content=rh_ke.content,
            tier="normal", namespace_id=ns.id, entry_type="route_hint",
            status="canonical", payload=rh_payload,
        )
        return ns.id, ns.slug


# ════════════════════════════════════════════
#  tests
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_load_all_knowledge_emits_full_span_io(
    async_session, seeded_full_bundle, monkeypatch,
):
    """span_io 必含 5 个 output keys + name + question/ns_slug input."""
    captured: list[dict] = []

    def capture(**kwargs):
        captured.append(dict(kwargs))

    monkeypatch.setattr(
        "app.knowledge.knowledge_loader.record_span_io", capture,
    )

    ns_id, ns_slug = seeded_full_bundle
    async with async_session() as db:
        await load_all_knowledge(db, ns_id, ns_slug, "商品有多少本")

    assert len(captured) == 1, f"record_span_io 被调用 {len(captured)} 次, 期望 1 次"
    span = captured[0]

    inp = span.get("input", {})
    out = span.get("output", {})

    # input 契约
    assert "question" in inp, f"input 缺 question, got keys={list(inp.keys())}"
    assert "ns_slug" in inp, f"input 缺 ns_slug, got keys={list(inp.keys())}"
    assert inp["ns_slug"] == ns_slug

    # output 3 keys 契约 (telemetry 字段名稳定性)
    # 注: terminology 由 AC 自动机 (match_terminology + batch_load_terminology) 在 query 路径处理,
    # 不经 load_all_knowledge, 因此 span 不含 term_count / term_hit_ids.
    expected_out_keys = {
        "critical_count", "route_hint_count",
        "route_hint_hit_ids",
    }
    actual_out_keys = set(out.keys())
    missing = expected_out_keys - actual_out_keys
    assert not missing, f"output 缺 keys: {missing}; got {actual_out_keys}"

    # 类型契约: hit_ids 必须是 list[int] (telemetry 上游聚合可比对)
    assert isinstance(out["route_hint_hit_ids"], list)
    for x in out["route_hint_hit_ids"]:
        assert isinstance(x, int)

    # 计数与播种数据一致 (1 critical + 1 route_hint)
    # terminology 由 AC 自动机路径处理, 不经 load_all_knowledge
    assert out["critical_count"] == 1
    assert out["route_hint_count"] == 1

    # name 契约 (record_span_io 已扩展支持 name kwarg)
    assert span.get("name") == "load_all_knowledge"
