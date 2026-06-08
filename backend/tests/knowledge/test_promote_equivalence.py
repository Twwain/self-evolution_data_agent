"""T3 — promote 集成测试 (registry dispatch end-to-end).

7 case 覆盖 spec 04-implementation-plan.md T3 step 1 全套:
1. hash 一致 → multi_source_consistent
2. enum_values 集合等价 → enum_set:matched
3. field_description 一空一非空 → non_empty_wins:matched
4. sample_values 多源去重 → sample_values_union:matched
5. mongodb List<X> 嵌套结构等价 → mongo_struct:matched
6. 普通描述措辞差异 → semantic_llm:matched (mock LLM 返 equivalent=true)
7. 真分歧 (枚举值矛盾, 全 strategy miss) → conflict

通用电商域示例 (orders / products / users), 通过 CLAUDE.md 6 条产品化 checklist.
fixtures: tests/knowledge/conftest.py 提供 db_session (真 SQLite); 本文件
ns fixture 自给, 与 spec 04 §85 路径要求一致.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import pytest_asyncio

# side-effect: register equivalence rules into _REGISTRY
import app.knowledge.equivalence.checkers  # noqa: F401
from app.knowledge.canonical_candidate import write_canonical_candidate
from app.knowledge.canonical_promote import promote_candidates_to_canonical
from app.models import Namespace

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def ns(db_session):
    """单 namespace 工厂."""
    obj = Namespace(slug="eqv_test", name="Equivalence Test NS")
    db_session.add(obj)
    await db_session.flush()
    await db_session.refresh(obj)
    return obj


async def _write_candidate(
    db, ns_id, *, target, field_path, kind, value, source, db_type="mysql",
):
    await write_canonical_candidate(
        db, namespace_id=ns_id, db_type=db_type, database="db1",
        target=target, field_path=field_path, candidate_kind=kind,
        candidate_value=value,
        evidence_sources=[{"source": source}],
        confidence_status="confirmed_by_code",
    )


async def test_hash_consistent_multi_source(db_session, ns):
    """case 1: 字面 value 完全一致 → multi_source_consistent (不走 registry)."""
    val = {"description": "用户订单状态"}
    await _write_candidate(db_session, ns.id, target="orders", field_path="status",
                            kind="field_description", value=val, source="repo_a")
    await _write_candidate(db_session, ns.id, target="orders", field_path="status",
                            kind="field_description", value=val, source="repo_b")
    await db_session.commit()
    report = await promote_candidates_to_canonical(db_session, ns.id)
    await db_session.commit()
    assert report.promoted_count == 1
    assert report.conflicted_count == 0


async def test_enum_set_equivalent(db_session, ns):
    """case 2: 同 enum 集合 (顺序无关) → enum_set:matched."""
    enum_a = [{"name": "PENDING", "db_value": 1}, {"name": "PAID", "db_value": 2}]
    enum_b = [{"name": "PAID", "db_value": 2}, {"name": "PENDING", "db_value": 1}]
    await _write_candidate(db_session, ns.id, target="orders", field_path="status",
                            kind="enum_values", value={"enum_values": enum_a}, source="code_enum")
    await _write_candidate(db_session, ns.id, target="orders", field_path="status",
                            kind="enum_values", value={"enum_values": enum_b}, source="introspect")
    await db_session.commit()
    report = await promote_candidates_to_canonical(db_session, ns.id)
    await db_session.commit()
    assert report.promoted_count == 1
    assert report.conflicted_count == 0


async def test_non_empty_wins(db_session, ns):
    """case 3: field_description 一空一非空 → non_empty_wins:matched."""
    await _write_candidate(db_session, ns.id, target="products", field_path="title",
                            kind="field_description", value={"description": ""}, source="repo_a")
    await _write_candidate(db_session, ns.id, target="products", field_path="title",
                            kind="field_description", value={"description": "商品标题"}, source="repo_b")
    await db_session.commit()
    report = await promote_candidates_to_canonical(db_session, ns.id)
    await db_session.commit()
    assert report.promoted_count == 1
    assert report.conflicted_count == 0


async def test_sample_values_union(db_session, ns):
    """case 4: 多源 sample_values → sample_values_union:matched, 去重并集."""
    await _write_candidate(db_session, ns.id, target="orders", field_path="status",
                            kind="sample_values",
                            value={"sample_values": ["pending", "shipped"]}, source="repo_a")
    await _write_candidate(db_session, ns.id, target="orders", field_path="status",
                            kind="sample_values",
                            value={"sample_values": ["shipped", "delivered"]}, source="repo_b")
    await db_session.commit()
    report = await promote_candidates_to_canonical(db_session, ns.id)
    await db_session.commit()
    assert report.promoted_count == 1
    assert report.conflicted_count == 0


async def test_mongo_struct_nested_equivalent(db_session, ns):
    """case 5: mongodb List<X> 嵌套结构等价 (sub_fields 一致) → mongo_struct:matched.

    通用业务字段示例: 订单条目列表 (List<OrderItem>), 不同 repo 解析顺序不同
    但子字段结构一致.
    """
    items_a = {
        "type": "List",
        "sub_fields": [
            {"name": "sku", "type": "string"},
            {"name": "qty", "type": "int"},
        ],
    }
    items_b = {
        "type": "List",
        "sub_fields": [
            {"name": "qty", "type": "int"},
            {"name": "sku", "type": "string"},
        ],
    }
    await _write_candidate(db_session, ns.id, target="orders", field_path="items",
                            kind="field_description", db_type="mongodb",
                            value=items_a, source="repo_a")
    await _write_candidate(db_session, ns.id, target="orders", field_path="items",
                            kind="field_description", db_type="mongodb",
                            value=items_b, source="repo_b")
    await db_session.commit()
    report = await promote_candidates_to_canonical(db_session, ns.id)
    await db_session.commit()
    assert report.promoted_count == 1
    assert report.conflicted_count == 0


async def test_semantic_llm_fallback(db_session, ns):
    """case 6: field_description 都非空且不同 → 走 semantic_llm 兜底.

    mock LLM 返 equivalent=true confidence=0.9, 应 promoted_count=1.
    """
    await _write_candidate(db_session, ns.id, target="orders", field_path="total",
                            kind="field_description",
                            value={"description": "订单总金额"}, source="repo_a")
    await _write_candidate(db_session, ns.id, target="orders", field_path="total",
                            kind="field_description",
                            value={"description": "订单总价格"}, source="repo_b")
    await db_session.commit()

    fake_response = json.dumps(
        {"equivalent": True, "confidence": 0.9, "reason": "措辞差异语义同"},
        ensure_ascii=False,
    )
    target_path = "app.knowledge.equivalence.strategies.semantic_llm._call_llm"
    with patch(target_path, return_value=fake_response):
        from app.knowledge.equivalence.strategies.semantic_llm import _SemanticBudget
        _SemanticBudget.reset()
        report = await promote_candidates_to_canonical(db_session, ns.id)
    await db_session.commit()
    assert report.promoted_count == 1, "semantic_llm 应判等价 → promote"
    assert report.conflicted_count == 0


async def test_real_conflict_all_miss(db_session, ns):
    """case 7: 枚举值矛盾 (同 name 不同 db_value) → 全 strategy miss → conflict.

    semantic_llm 返 false, 链全部 miss, 写 SchemaCanonicalConflict.
    """
    enum_a = [{"name": "STATUS_A", "db_value": 1}]
    enum_b = [{"name": "STATUS_A", "db_value": 99}]
    await _write_candidate(db_session, ns.id, target="orders", field_path="state",
                            kind="enum_values", value={"enum_values": enum_a}, source="repo_a")
    await _write_candidate(db_session, ns.id, target="orders", field_path="state",
                            kind="enum_values", value={"enum_values": enum_b}, source="repo_b")
    await db_session.commit()

    fake_response = json.dumps(
        {"equivalent": False, "confidence": 0.95, "reason": "db_value 矛盾"},
        ensure_ascii=False,
    )
    target_path = "app.knowledge.equivalence.strategies.semantic_llm._call_llm"
    with patch(target_path, return_value=fake_response):
        from app.knowledge.equivalence.strategies.semantic_llm import _SemanticBudget
        _SemanticBudget.reset()
        report = await promote_candidates_to_canonical(db_session, ns.id)
    await db_session.commit()
    assert report.promoted_count == 0
    assert report.conflicted_count == 1
