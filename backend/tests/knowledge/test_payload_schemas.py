"""5 类 payload Pydantic schemas 校验测试 — 真实环境无 mock"""
import pytest
from pydantic import ValidationError

from app.schemas.knowledge_payload import (
    TerminologyPayload, ExamplePayload, RulePayload,
    RouteHintPayload, parse_payload,
)


def test_terminology_payload_minimal():
    p = TerminologyPayload(
        term="条目",
        primary_collection="c_sku",
        primary_database="order_db",
        db_type="mongodb",
    )
    assert p.term == "条目"
    assert p.synonyms == []
    assert p.primary_collection == "c_sku"


def test_terminology_payload_full():
    p = TerminologyPayload(
        term="订单", synonyms=["order", "订单"],
        primary_collection="c_product", primary_database="order_db",
        db_type="mongodb",
        primary_field="categoryId", source_collections=["c_product"],
    )
    assert p.synonyms == ["order", "订单"]
    assert p.primary_field == "categoryId"


def test_example_payload_requires_question_and_collection():
    with pytest.raises(ValidationError):
        ExamplePayload(query_json={})  # type: ignore[call-arg]  # 故意缺 required, 测 ValidationError


def test_example_payload_full():
    p = ExamplePayload(
        question="昨天订单数",
        target_collection="c_product",
        query_json={"find": {"createdAt": {"$gte": "2026-04-28"}}},
        result_summary="返回 1 行",
        source_query_history_id=42,
    )
    assert p.target_collection == "c_product"
    assert p.source_query_history_id == 42


def test_rule_payload():
    p = RulePayload(rule_text="订单必须含 categoryId", priority=1)
    assert p.applies_to_collections == []
    assert p.priority == 1


def test_route_hint_payload():
    p = RouteHintPayload(
        question_pattern="商品→订单→条目",
        collection_path=["c_category", "c_product", "c_sku"],
        join_fields=[{"from": "c_category._id", "to": "c_product.categoryId"}],
        cost_strategy="batched_count_only",
        reason="多层关联用分批",
    )
    assert p.cost_strategy == "batched_count_only"
    assert len(p.collection_path) == 3


def test_parse_payload_dispatch():
    """parse_payload(entry_type, raw_dict) → 对应 Pydantic 实例"""
    p = parse_payload("terminology", {
        "term": "条目",
        "primary_collection": "c_sku",
        "primary_database": "order_db",
        "db_type": "mongodb",
    })
    assert isinstance(p, TerminologyPayload)

    p = parse_payload("rule", {"rule_text": "x"})
    assert isinstance(p, RulePayload)

    with pytest.raises(ValueError):
        parse_payload("unknown_type", {})
