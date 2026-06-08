"""
Task 3.2 — 录入向量化 + 分层召回测试 (Stage 2 Task 1 重写)

历史: 旧版用 unittest.mock 包裹 ChromaDB, 但项目纪律 §06 要求知识层测试用真实 SQLite
+ 真实 ChromaDB. Stage 2 Task 1 升级 _retrieve_layer3 → list[KnowledgeHit] 同时把本文件
重写为 chroma_isolated fixture 模式, 与 tests/knowledge/test_status_state_machine.py 对齐.

覆盖:
- upsert: namespace 写入 / global slug 兜底 / critical tier skip / status=proposed skip
- delete: 命名空间删除 / 全局删除 / 集合不存在静默跳过
- _retrieve_layer3: critical 优先 (本测试 normal 覆盖, critical 走 SQL 不进 ChromaDB)
                   / 去重 / 全空降级
"""
import pytest

from app.knowledge.knowledge_retriever import (
    KnowledgeHit,
    delete_knowledge_entry,
    make_doc_id,
    _retrieve_layer3,
    upsert_knowledge_entry,
)


# ── fixtures ──────────────────────────────────────────────

@pytest.fixture
def chroma_isolated(tmp_path, monkeypatch):
    """每个测试用独立 ChromaDB 持久化目录, 重置 registry 单例."""
    monkeypatch.setattr(
        "app.config.settings.chroma_persist_dir", str(tmp_path / "chroma")
    )
    import app.engine.registry as reg
    reg._chroma_client = None
    yield
    reg._chroma_client = None


# ════════════════════════════════════════════
#  upsert 测试
# ════════════════════════════════════════════

def test_upsert_namespace_entry(chroma_isolated):
    """命名空间知识 → 写入 ns_{slug}_knowledge, doc_id = ke_{id}."""
    from app.engine.registry import get_knowledge_collection

    upsert_knowledge_entry(
        slug="demo", entry_id=42, content="业务术语: 已删除=软删除标志",
        tier="normal", namespace_id=1, entry_type="terminology",
        payload={
            "term": "已删除",
            "primary_collection": "c_test",
            "primary_database": "db_test",
            "db_type": "mongodb",
            "synonyms": [],
            "source_collections": [],
        },
    )
    coll = get_knowledge_collection("demo")
    # terminology 多向量: doc_id = ke_{id}_{index} (index=0 是 term)
    res = coll.get(ids=["ke_42_0"])
    assert res["ids"] == ["ke_42_0"]
    assert "已删除" in res["documents"][0]


def test_upsert_global_entry_uses_global_slug(chroma_isolated):
    """全局知识 (namespace_id=None) + normal tier → 写入 ns___global___knowledge."""
    from app.engine.registry import get_knowledge_collection

    upsert_knowledge_entry(
        slug="anything", entry_id=7, content="全局规则",
        tier="normal", namespace_id=None, entry_type="rule",
    )
    coll = get_knowledge_collection("__global__")
    res = coll.get(ids=[make_doc_id(7)])
    assert res["ids"] == ["ke_7"]
    # namespace_id=None → -1 sentinel 落 metadata
    assert res["metadatas"][0]["namespace_id"] == -1


def test_upsert_critical_tier_skipped(chroma_isolated):
    """critical tier 不进向量池 — 集合内零文档."""
    from app.engine.registry import get_knowledge_collection

    upsert_knowledge_entry(
        slug="demo", entry_id=99, content="核心约束",
        tier="critical", namespace_id=1, entry_type="rule",
    )
    coll = get_knowledge_collection("demo")
    assert coll.count() == 0


def test_upsert_proposed_status_skipped(chroma_isolated):
    """status=proposed 不进 RAG — 集合内零文档."""
    from app.engine.registry import get_knowledge_collection

    upsert_knowledge_entry(
        slug="demo", entry_id=100, content="待审知识",
        tier="normal", namespace_id=1,
        entry_type="rule", status="proposed",
    )
    coll = get_knowledge_collection("demo")
    assert coll.count() == 0


# ════════════════════════════════════════════
#  delete 测试
# ════════════════════════════════════════════

def test_delete_namespace_entry(chroma_isolated):
    """命名空间知识删除 → ChromaDB 中该 doc_id 不存在."""
    from app.engine.registry import get_knowledge_collection

    upsert_knowledge_entry(
        slug="demo", entry_id=42, content="待删除",
        tier="normal", namespace_id=1, entry_type="rule",
    )
    coll = get_knowledge_collection("demo")
    assert coll.get(ids=["ke_42"])["ids"] == ["ke_42"]

    delete_knowledge_entry(slug="demo", entry_id=42, namespace_id=1)
    assert coll.get(ids=["ke_42"])["ids"] == []


def test_delete_global_entry(chroma_isolated):
    """全局知识删除 → 查 __global__ 集合."""
    from app.engine.registry import get_knowledge_collection

    upsert_knowledge_entry(
        slug="x", entry_id=7, content="全局",
        tier="normal", namespace_id=None, entry_type="rule",
    )
    coll = get_knowledge_collection("__global__")
    assert coll.get(ids=["ke_7"])["ids"] == ["ke_7"]

    delete_knowledge_entry(slug="anything", entry_id=7, namespace_id=None)
    assert coll.get(ids=["ke_7"])["ids"] == []


def test_delete_silently_skips_missing_collection(chroma_isolated):
    """集合不存在时静默跳过, 不抛异常."""
    # 集合从未创建 → 不应抛
    delete_knowledge_entry(slug="never_existed", entry_id=99, namespace_id=1)


# ════════════════════════════════════════════
#  _retrieve_layer3 测试
# ════════════════════════════════════════════

def test__retrieve_layer3_returns_knowledge_hit(chroma_isolated):
    """_retrieve_layer3 返回 KnowledgeHit dataclass, 字段从 metadata 还原."""
    upsert_knowledge_entry(
        slug="demo", entry_id=1, content="normal-doc-content",
        tier="normal", namespace_id=1, entry_type="rule",
    )
    result = _retrieve_layer3("demo", "查询问题")
    assert len(result) >= 1
    assert isinstance(result[0], KnowledgeHit)
    assert result[0].content == "normal-doc-content"
    assert result[0].tier == "normal"


def test__retrieve_layer3_deduplicates(chroma_isolated):
    """同一 entry_id 跨层出现时按 entry_id 去重 (本场景 ns 集合 1 条 + global 0 条)."""
    upsert_knowledge_entry(
        slug="demo", entry_id=11, content="shared-doc",
        tier="normal", namespace_id=1, entry_type="rule",
    )
    result = _retrieve_layer3("demo", "question")
    # 同一 entry_id 不重复
    ids = [h.entry_id for h in result]
    assert ids.count(11) == 1


def test__retrieve_layer3_graceful_on_empty(chroma_isolated):
    """所有集合为空时返回 [] (集合从未创建 → get_collection 抛 → 静默)."""
    result = _retrieve_layer3("never_existed_slug", "question")
    assert result == []
