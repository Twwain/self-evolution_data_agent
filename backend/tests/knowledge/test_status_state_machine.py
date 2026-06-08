"""ChromaDB 写入只在 status=canonical 时触发 — 真实 ChromaDB.

Stage 1 Task 6 — knowledge_retriever 升级支持 status 过滤.
完整状态机 e2e (proposed → canonical → superseded) 在 Stage 3 覆盖.

`chroma_isolated` fixture 共享自 tests/knowledge/conftest.py.
"""

from app.knowledge.knowledge_retriever import (
    delete_knowledge_entry,
    upsert_knowledge_entry,
)


# ── tests ─────────────────────────────────────────────────

def test_upsert_skips_non_canonical(chroma_isolated):
    """status != canonical 不触发 ChromaDB upsert."""
    from app.engine.registry import get_knowledge_collection

    upsert_knowledge_entry(
        slug="test",
        entry_id=1,
        content="x",
        tier="normal",
        namespace_id=1,
        entry_type="terminology",
        status="proposed",
    )
    coll = get_knowledge_collection("test")
    assert coll.count() == 0


def test_upsert_writes_canonical(chroma_isolated):
    """status=canonical 正常 upsert."""
    from app.engine.registry import get_knowledge_collection

    upsert_knowledge_entry(
        slug="test",
        entry_id=2,
        content="y",
        tier="normal",
        namespace_id=1,
        entry_type="rule",
        status="canonical",
    )
    coll = get_knowledge_collection("test")
    assert coll.count() == 1


def test_status_change_to_superseded_triggers_delete(chroma_isolated):
    """canonical → superseded 通过 delete_knowledge_entry 清理向量."""
    from app.engine.registry import get_knowledge_collection

    upsert_knowledge_entry(
        slug="test",
        entry_id=3,
        content="z",
        tier="normal",
        namespace_id=1,
        entry_type="terminology",
        status="canonical",
    )
    delete_knowledge_entry(slug="test", entry_id=3, namespace_id=1)
    coll = get_knowledge_collection("test")
    assert coll.count() == 0


def test_critical_canonical_still_skips(chroma_isolated):
    """tier=critical + status=canonical 仍跳过 (由 executor._load_layer1_knowledge 直接 SQL 加载)."""
    from app.engine.registry import get_knowledge_collection

    upsert_knowledge_entry(
        slug="test",
        entry_id=4,
        content="critical-rule",
        tier="critical",
        namespace_id=1,
        entry_type="rule",
        status="canonical",
    )
    coll = get_knowledge_collection("test")
    assert coll.count() == 0


def test_metadata_contains_status(chroma_isolated):
    """metadata 写入 status 字段, 供 retrieve_layer3 按 status 过滤 (Task 12 启用)."""
    from app.engine.registry import get_knowledge_collection

    upsert_knowledge_entry(
        slug="test",
        entry_id=5,
        content="m",
        tier="normal",
        namespace_id=1,
        entry_type="rule",
        status="canonical",
    )
    coll = get_knowledge_collection("test")
    got = coll.get(ids=["ke_5"])
    metas = got.get("metadatas") or []
    assert metas and metas[0]["status"] == "canonical"
    assert metas[0]["entry_type"] == "rule"
    assert metas[0]["tier"] == "normal"
