"""
KnowledgeEntry 新字段测试 — tier / description / raw_input / refined_at / is_superseded
验证持久化默认值 + 显式赋值的正确性
"""

import pytest
from app.models import KnowledgeEntry


@pytest.mark.asyncio
async def test_knowledge_entry_new_fields_defaults(db):
    e = KnowledgeEntry(entry_type="terminology", content="GMV = 含退款总成交额")
    db.add(e)
    await db.commit()
    await db.refresh(e)

    assert e.tier == "normal"
    assert e.description == ""
    assert e.raw_input == ""
    assert e.refined_at is None
    assert e.is_superseded is False


@pytest.mark.asyncio
async def test_knowledge_entry_tier_critical(db):
    e = KnowledgeEntry(
        entry_type="terminology",
        content="订单状态=1 表示已支付",
        tier="critical",
        description="订单支付口径",
        raw_input="订单要是支付过的那种",
    )
    db.add(e)
    await db.commit()
    await db.refresh(e)

    assert e.tier == "critical"
    assert e.description == "订单支付口径"
    assert e.raw_input == "订单要是支付过的那种"
    assert e.is_superseded is False
    assert e.refined_at is None
