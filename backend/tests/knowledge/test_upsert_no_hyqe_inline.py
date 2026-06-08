"""Phase 0 — upsert_knowledge_entry 不再生成 hq_*.

验证 rule/route_hint 走单向量路径, upsert 不调 generate_hypothetical_queries,
不写 hq_* 子向量, 返回 None.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_upsert_route_hint_does_not_call_llm():
    """rule/route_hint 走单向量路径, upsert 不调 generate_hypothetical_queries."""
    from app.knowledge.knowledge_retriever import upsert_knowledge_entry

    fake_coll = MagicMock()
    with patch(
        "app.engine.registry.get_knowledge_collection",
        return_value=fake_coll,
    ), patch(
        "app.knowledge.hypothetical_queries.generate_hypothetical_queries",
    ) as gen_spy:
        ret = upsert_knowledge_entry(
            slug="test_ns", entry_id=42,
            content="路由提示", tier="normal",
            namespace_id=1, entry_type="route_hint", status="canonical",
            payload=None,
        )

    gen_spy.assert_not_called()
    assert ret is None


@pytest.mark.asyncio
async def test_upsert_route_hint_writes_only_main_vector():
    """upsert rule/route_hint 只写主向量, 不带 hq_*."""
    from app.knowledge.knowledge_retriever import upsert_knowledge_entry

    fake_coll = MagicMock()
    with patch(
        "app.engine.registry.get_knowledge_collection",
        return_value=fake_coll,
    ):
        upsert_knowledge_entry(
            slug="test_ns", entry_id=42,
            content="x", tier="normal",
            namespace_id=1, entry_type="route_hint", status="canonical",
            payload=None,
        )

    # 主向量写入, ids 长度 = 1
    upsert_calls = fake_coll.upsert.call_args_list
    assert len(upsert_calls) == 1
    ids = upsert_calls[0].kwargs["ids"]
    assert len(ids) == 1
    assert "_hq_" not in ids[0]


@pytest.mark.asyncio
async def test_upsert_rule_does_not_call_llm():
    """rule 类型同样不调 LLM."""
    from app.knowledge.knowledge_retriever import upsert_knowledge_entry

    fake_coll = MagicMock()
    with patch(
        "app.engine.registry.get_knowledge_collection",
        return_value=fake_coll,
    ), patch(
        "app.knowledge.hypothetical_queries.generate_hypothetical_queries",
    ) as gen_spy:
        ret = upsert_knowledge_entry(
            slug="test_ns", entry_id=99,
            content="规则", tier="normal",
            namespace_id=1, entry_type="rule", status="canonical",
            payload=None,
        )

    gen_spy.assert_not_called()
    assert ret is None
