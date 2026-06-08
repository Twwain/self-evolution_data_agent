"""P0-3 Task 4: save_knowledge emit knowledge_proposed 测试."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from app.models.namespace import Namespace


@pytest.mark.asyncio
async def test_save_knowledge_emits_knowledge_proposed_for_non_terminology(
    db_session,
):
    """save_knowledge non-terminology 路径 flush 后 emit knowledge_proposed."""
    from app.engine.tools.knowledge_tools import save_knowledge

    ns = Namespace(slug="emit_knowledge_ns", name="emit_knowledge")
    db_session.add(ns)
    await db_session.commit()

    fake_emit = AsyncMock()
    result = await save_knowledge(
        db=db_session, namespace_id=ns.id, ns_slug=ns.slug,
        sse_emit=fake_emit,
        entry_type="rule",
        content="不允许超过 6 个月数据范围",
        payload={"rule_text": "查询数据范围不允许超过 6 个月"},
        evidence={},
    )
    assert "entry_id" in result
    fake_emit.assert_awaited()
    emitted = [c.args[0] for c in fake_emit.await_args_list]
    proposed = [e for e in emitted if e.get("event") == "knowledge_proposed"]
    assert len(proposed) == 1
    data = proposed[0]["data"]
    assert data["entry_id"] == result["entry_id"]
    assert data["entry_type"] == "rule"
    assert "preview" in data


@pytest.mark.asyncio
async def test_save_knowledge_no_emit_on_terminology_validation_failure(
    db_session,
):
    """terminology 闸门拒绝 (None ke) 不 emit — 无 entry_id 可通报."""
    from unittest.mock import patch
    from app.engine.tools.knowledge_tools import save_knowledge

    ns = Namespace(slug="emit_knowledge_reject_ns", name="emit_knowledge_reject")
    db_session.add(ns)
    await db_session.commit()

    fake_emit = AsyncMock()
    with patch(
        "app.knowledge.terminology_intake.upsert_terminology_with_validation",
        return_value=None,
    ):
        result = await save_knowledge(
            db=db_session, namespace_id=ns.id, ns_slug=ns.slug,
            sse_emit=fake_emit,
            entry_type="terminology",
            content="term=xxx",
            payload={"term": "xxx", "definition": "y", "db_type": "mongo"},
            evidence={},
        )

    assert result.get("success") is False
    # 闸门拒绝不 emit
    emitted = [c.args[0] for c in fake_emit.await_args_list]
    proposed = [e for e in emitted if e.get("event") == "knowledge_proposed"]
    assert len(proposed) == 0
