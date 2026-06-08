"""P0-3 Task 4: clarify_with_user emit clarify_request 测试."""
from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock

from app.models.namespace import Namespace


@pytest.mark.asyncio
async def test_clarify_with_user_emits_clarify_request(db_session):
    """commit 后立即 emit clarify_request 事件, 含必要字段."""
    from app.engine.tools.interaction_tools import (
        _pending_answers,
        _pending_events,
        clarify_with_user,
    )

    ns = Namespace(slug="emit_clarify_ns", name="emit_clarify")
    db_session.add(ns)
    await db_session.commit()

    fake_emit = AsyncMock()
    trace_id = "trace-clarify-emit-t4"

    async def resolver():
        await asyncio.sleep(0.05)
        _pending_answers[trace_id] = "yes"
        if (ev := _pending_events.get(trace_id)):
            ev.set()

    asyncio.create_task(resolver())

    result = await clarify_with_user(
        db=db_session, trace_id=trace_id, namespace_id=ns.id,
        sse_emit=fake_emit,
        question="是否继续？", options=["yes", "no"], reason="数据量大",
    )

    assert result["timeout"] is False
    fake_emit.assert_awaited()
    emitted = [c.args[0] for c in fake_emit.await_args_list]
    clarify_evts = [e for e in emitted if e.get("event") == "clarify_request"]
    assert len(clarify_evts) == 1
    data = clarify_evts[0]["data"]
    assert "pending_id" in data
    assert data["question"] == "是否继续？"
    assert data["options"] == ["yes", "no"]
    assert data["reason"] == "数据量大"
