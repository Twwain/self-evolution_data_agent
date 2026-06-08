"""Stage 2 Task 1 — _retrieve_layer3 升级 e2e (真实 SQLite + ChromaDB).

升级目标:
- 返回 list[KnowledgeHit] dataclass (entry_id / content / entry_type / status / payload / distance / tier / namespace_id)
- entry_types 过滤: agent loop 场景按 5 类宪章隔离 (terminology / rule / example / route_hint / schema_summary)
- status 防御性二次过滤 (默认 canonical)
- k 值默认从 settings.knowledge_retrieve_critical_n / _normal_n 读取, 可 keyword-only 覆盖

`chroma_isolated` fixture 共享自 tests/knowledge/conftest.py.
"""

from app.knowledge.knowledge_retriever import (
    KnowledgeHit,
    _retrieve_layer3,
    upsert_knowledge_entry,
)


# ── tests ─────────────────────────────────────────────────

def test_returns_knowledge_hit_dataclass(chroma_isolated):
    """KnowledgeHit dataclass 字段完整, entry_id 从 doc_id 解析, metadata 字段透出."""
    upsert_knowledge_entry(
        slug="ns1", entry_id=1, content="custom_orders 是订单表",
        tier="normal", namespace_id=1,
        entry_type="rule", status="canonical",
    )
    hits = _retrieve_layer3("ns1", "订单")
    assert len(hits) >= 1
    h = hits[0]
    assert isinstance(h, KnowledgeHit)
    assert h.entry_id == 1
    assert h.entry_type == "rule"
    assert h.status == "canonical"
    assert h.tier == "normal"
    assert h.namespace_id == 1
    assert h.distance >= 0
    assert h.content == "custom_orders 是订单表"


def test_status_filter_excludes_proposed(chroma_isolated):
    """proposed 不入 ChromaDB → 检索零命中 (status=canonical 防御性过滤)."""
    upsert_knowledge_entry(
        slug="ns1", entry_id=2, content="proposed 不该召回",
        tier="normal", namespace_id=1,
        entry_type="terminology", status="proposed",
    )
    hits = _retrieve_layer3("ns1", "proposed")
    # upsert_knowledge_entry 内部 skip → 集合本身就不存在该 doc
    # 即便存在, 也应被 status=canonical where 过滤
    assert all(h.status == "canonical" for h in hits)
    assert all(h.entry_id != 2 for h in hits)


def test_entry_types_filter_isolates_terminology_vs_rule(chroma_isolated):
    """entry_types 过滤: 仅召回指定类型 (agent loop 隔离场景)."""
    upsert_knowledge_entry(
        slug="ns1", entry_id=10, content="术语映射 customer 对应 客户表",
        tier="normal", namespace_id=1,
        entry_type="terminology", status="canonical",
        payload={
            "term": "customer", "primary_collection": "customer",
            "primary_database": "db_q", "db_type": "mongodb",
            "synonyms": ["客户"], "source_collections": ["customer"],
        },
    )
    upsert_knowledge_entry(
        slug="ns1", entry_id=11, content="规则: 查询必须含 namespace 过滤条件",
        tier="normal", namespace_id=1,
        entry_type="rule", status="canonical",
    )

    hits_term = _retrieve_layer3("ns1", "客户", entry_types=["terminology"])
    assert all(h.entry_type == "terminology" for h in hits_term)
    assert any(h.entry_id == 10 for h in hits_term)

    hits_rule = _retrieve_layer3("ns1", "namespace", entry_types=["rule"])
    assert all(h.entry_type == "rule" for h in hits_rule)
    assert any(h.entry_id == 11 for h in hits_rule)


def test_normal_tier_recall_works(chroma_isolated):
    """normal tier 召回正常 — critical 由 SQL 直接加载, ChromaDB 仅存 normal."""
    upsert_knowledge_entry(
        slug="ns1", entry_id=20, content="normal_term_recall",
        tier="normal", namespace_id=1,
        entry_type="rule", status="canonical",
    )
    hits = _retrieve_layer3("ns1", "normal_term_recall")
    assert len(hits) >= 1
    assert hits[0].tier == "normal"


def test_global_ns_fallback(chroma_isolated):
    """命名空间无命中 → 全局 __global__ 集合兜底, namespace_id=-1 (sentinel) 还原为 None."""
    upsert_knowledge_entry(
        slug="__global__", entry_id=30, content="全局通用术语 customer",
        tier="normal", namespace_id=None,  # 全局
        entry_type="rule", status="canonical",
    )
    hits = _retrieve_layer3("ns_empty", "customer")
    matched = [h for h in hits if h.entry_id == 30]
    assert len(matched) == 1
    assert matched[0].namespace_id is None  # -1 sentinel 还原回 None


def test_settings_config_drives_k_values(chroma_isolated, monkeypatch):
    """改 settings.knowledge_retrieve_normal_n=2 → 最多召回 2 条 normal (默认 k 来自 settings)."""
    monkeypatch.setattr("app.config.settings.knowledge_retrieve_normal_n", 2)
    monkeypatch.setattr("app.config.settings.knowledge_retrieve_critical_n", 2)
    for i in range(5):
        upsert_knowledge_entry(
            slug="ns1", entry_id=40 + i, content=f"term_{i}_recall_token",
            tier="normal", namespace_id=1,
            entry_type="rule", status="canonical",
        )
    hits = _retrieve_layer3("ns1", "term recall token")
    # ns1 normal 最多召回 2 条 (global tier 也是 2 但 __global__ 集合空)
    # critical 走 SQL 不进 ChromaDB, 不计入
    assert len(hits) <= 2
