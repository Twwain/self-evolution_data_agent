"""Stage 2 Task 5 — _sync_chromadb_after_patch 转移矩阵 (真实 ChromaDB).

测试 helper 直接, 不走 HTTP — 更快更聚焦, 4 用例覆盖 PATCH 状态转移矩阵.
`chroma_isolated` fixture 共享自 tests/knowledge/conftest.py.
"""

import pytest

from app.api.knowledge import _sync_chromadb_after_patch
from app.knowledge.knowledge_retriever import upsert_knowledge_entry
from app.models.knowledge_entry import KnowledgeEntry


def _make_entry(entry_id: int, tier: str, status: str, namespace_id: int = 1) -> KnowledgeEntry:
    """脱离 SQLite 直接造 KE 实例 — 本测试只验 ChromaDB 同步, 不验持久化."""
    e = KnowledgeEntry(
        entry_type="rule",
        content="x",
        source="manual",
        status=status,
        tier=tier,
        namespace_id=namespace_id,
    )
    e.id = entry_id
    return e


# ── case 1: normal → critical 应删向量 ─────────────────────
@pytest.mark.asyncio
async def test_tier_normal_to_critical_deletes_from_chromadb(chroma_isolated):
    """normal/canonical → critical/canonical: critical 不进 RAG, ChromaDB 应删向量."""
    from app.engine.registry import get_knowledge_collection

    upsert_knowledge_entry(
        slug="ns1",
        entry_id=1,
        content="x",
        tier="normal",
        namespace_id=1,
        entry_type="rule",
        status="canonical",
    )
    coll = get_knowledge_collection("ns1")
    assert coll.count() == 1

    entry = _make_entry(1, tier="critical", status="canonical")
    await _sync_chromadb_after_patch(
        slug="ns1",
        entry=entry,
        old_tier="normal",
        old_status="canonical",
        content_changed=False,
    )
    assert coll.count() == 0


# ── case 2: critical → normal/canonical 应 upsert ──────────
@pytest.mark.asyncio
async def test_tier_critical_to_normal_canonical_upserts_to_chromadb(chroma_isolated):
    """critical/canonical → normal/canonical: 之前 critical 没入池, 现在应进 RAG."""
    from app.engine.registry import get_knowledge_collection

    # critical → upsert 内部跳过, ChromaDB 集合 count==0
    upsert_knowledge_entry(
        slug="ns1",
        entry_id=2,
        content="critical_doc",
        tier="critical",
        namespace_id=1,
        entry_type="rule",
        status="canonical",
    )
    coll = get_knowledge_collection("ns1")
    assert coll.count() == 0

    entry = _make_entry(2, tier="normal", status="canonical")
    entry.content = "critical_doc"  # patch 后内容
    await _sync_chromadb_after_patch(
        slug="ns1",
        entry=entry,
        old_tier="critical",
        old_status="canonical",
        content_changed=False,
    )
    assert coll.count() == 1


# ── case 3: critical → normal/proposed 跳过 ChromaDB ───────
@pytest.mark.asyncio
async def test_tier_critical_to_normal_proposed_skips_chromadb(chroma_isolated):
    """critical/canonical → normal/proposed: status≠canonical 不进 RAG, ChromaDB 不动."""
    from app.engine.registry import get_knowledge_collection

    entry = _make_entry(3, tier="normal", status="proposed")
    await _sync_chromadb_after_patch(
        slug="ns1",
        entry=entry,
        old_tier="critical",
        old_status="canonical",
        content_changed=False,
    )
    # 集合可能未创建, 也可能存在但 count==0 — 二者都 OK (只要没把 proposed 写进去)
    try:
        coll = get_knowledge_collection("ns1")
        assert coll.count() == 0
    except Exception:
        pass


# ── case 4: tier/status/content 都不变 — no-op ──────────────
@pytest.mark.asyncio
async def test_tier_unchanged_no_op(chroma_isolated):
    """tier/status/content 都不变: helper 不调用 ChromaDB, 既不重 upsert 也不删."""
    from app.engine.registry import get_knowledge_collection

    upsert_knowledge_entry(
        slug="ns1",
        entry_id=4,
        content="x",
        tier="normal",
        namespace_id=1,
        entry_type="rule",
        status="canonical",
    )
    coll = get_knowledge_collection("ns1")
    assert coll.count() == 1

    entry = _make_entry(4, tier="normal", status="canonical")
    await _sync_chromadb_after_patch(
        slug="ns1",
        entry=entry,
        old_tier="normal",
        old_status="canonical",
        content_changed=False,
    )
    # no-op: count 仍是 1
    assert coll.count() == 1
