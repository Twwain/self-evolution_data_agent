"""Phase 1 — save_knowledge schema 闸门 (parse_payload 接入).

验证:
- route_hint extra field → rejected
- rule invalid kind → rejected
- valid payload → 入库 + 字段不变形
"""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock

from app.engine.tools.knowledge_tools import save_knowledge
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import Namespace


@pytest.mark.asyncio
async def test_save_knowledge_route_hint_extra_field_rejected(db_session):
    """RouteHintPayload extra='forbid' 拦 LLM 多塞字段."""
    ns = Namespace(name="schema-gate-test", slug="schema-gate-test", description="")
    db_session.add(ns)
    await db_session.flush()

    result = await save_knowledge(
        db=db_session, namespace_id=ns.id, ns_slug=ns.slug,
        sse_emit=AsyncMock(),
        entry_type="route_hint",
        content="路由提示",
        payload={
            "question_pattern": "查询订单",
            "collection_path": ["c_orders"],
            "extra_random_field": "haha",
        },
        evidence={},
    )
    assert result.get("success") is False, f"应拒 extra 字段, got {result}"
    assert result.get("reason") == "validation_failed"


@pytest.mark.asyncio
async def test_save_knowledge_rule_invalid_kind_rejected(db_session):
    """RulePayload.rule_kind Literal 白名单外 reject."""
    ns = Namespace(name="schema-gate-test2", slug="schema-gate-test2", description="")
    db_session.add(ns)
    await db_session.flush()

    result = await save_knowledge(
        db=db_session, namespace_id=ns.id, ns_slug=ns.slug,
        sse_emit=AsyncMock(),
        entry_type="rule",
        content="规则",
        payload={
            "rule_text": "x",
            "rule_kind": "MYSTERY_KIND",
        },
        evidence={},
    )
    assert result.get("success") is False
    assert result.get("reason") == "validation_failed"


@pytest.mark.asyncio
async def test_save_knowledge_route_hint_valid_payload_persists(db_session):
    """合法 payload 入库 + 字段不变形 (闸门不注入默认值)."""
    ns = Namespace(name="schema-gate-test3", slug="schema-gate-test3", description="")
    db_session.add(ns)
    await db_session.flush()

    result = await save_knowledge(
        db=db_session, namespace_id=ns.id, ns_slug=ns.slug,
        sse_emit=AsyncMock(),
        entry_type="route_hint",
        content="路由提示",
        payload={
            "question_pattern": "订单关联用户",
            "collection_path": ["c_orders", "c_users"],
            "join_fields": [{"a": "c_orders.user_id", "b": "c_users.id"}],
            "cost_strategy": "default",
            "reason": "二层关联",
        },
        evidence={"source": "test"},
    )
    assert "entry_id" in result, f"应入库, got {result}"
    ke = await db_session.get(KnowledgeEntry, int(result["entry_id"]))
    assert ke is not None
    payload = json.loads(ke.payload)
    assert payload["collection_path"] == ["c_orders", "c_users"]
    assert payload["question_pattern"] == "订单关联用户"
    assert payload["cost_strategy"] == "default"


@pytest.mark.asyncio
async def test_save_knowledge_example_extra_field_rejected(db_session):
    """ExamplePayload extra='forbid' 拦 extra 字段."""
    ns = Namespace(name="schema-gate-test4", slug="schema-gate-test4", description="")
    db_session.add(ns)
    await db_session.flush()

    result = await save_knowledge(
        db=db_session, namespace_id=ns.id, ns_slug=ns.slug,
        sse_emit=AsyncMock(),
        entry_type="example",
        content="示例",
        payload={
            "question": "本月订单数",
            "target_collection": "c_orders",
            "query_json": {"aggregate": []},
            "bogus_field": "should_be_rejected",
        },
        evidence={},
    )
    assert result.get("success") is False
    assert result.get("reason") == "validation_failed"
