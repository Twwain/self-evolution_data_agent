"""P0-3 Task 4: /clarify_response 端点 emit clarify_resolved 测试."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_fake_pc(pending_id: int = 12345):
    """构造 PendingClarification mock 对象."""
    pc = MagicMock()
    pc.id = pending_id
    pc.namespace_id = 1
    pc.original_question = "测试问题"
    pc.clarification_questions_json = '[{"question":"哪个?","options":["a","b"],"reason":"不明"}]'
    return pc


@pytest.mark.asyncio
async def test_clarify_response_endpoint_emits_clarify_resolved():
    """submit_clarify_response 调 helper 后通过 event_q emit clarify_resolved."""
    from app.api.query import submit_clarify_response
    from app.engine.sse_manager import register_sse_session
    from app.schemas.query_stream import ClarifyResponseRequest

    trace_id = "trace-clarify-resolved-t4-test"
    event_q, _ = register_sse_session(trace_id)
    pending_id = 12345

    async def fake_resolve(*, db, pending_id, answer):
        return None

    fake_db = AsyncMock()
    fake_db.get.return_value = _make_fake_pc(pending_id)
    fake_user = object()

    with patch(
        "app.engine.tools.interaction_tools.resolve_pending_clarification",
        new=fake_resolve,
    ), patch("app.api.query._clarify_extract_hook", new=AsyncMock()):
        await submit_clarify_response(
            trace_id=trace_id,
            body=ClarifyResponseRequest(pending_id=pending_id, answer="yes"),
            user=fake_user,
            db=fake_db,
        )

    evt = await event_q.get()
    assert evt["event"] == "clarify_resolved"
    assert evt["data"]["pending_id"] == pending_id
    assert evt["data"]["answer"] == "yes"


@pytest.mark.asyncio
async def test_clarify_response_endpoint_no_error_when_sse_disconnected():
    """SSE 已断 (get_event_queue 返 None) 不抛错, 静默跳过."""
    from app.api.query import submit_clarify_response
    from app.schemas.query_stream import ClarifyResponseRequest

    trace_id = "trace-clarify-resolved-no-sse"
    # 不注册 SSE session → get_event_queue 返 None

    async def fake_resolve(*, db, pending_id, answer):
        return None

    fake_db = AsyncMock()
    fake_db.get.return_value = _make_fake_pc(999)
    fake_user = object()

    with patch(
        "app.engine.tools.interaction_tools.resolve_pending_clarification",
        new=fake_resolve,
    ), patch("app.api.query._clarify_extract_hook", new=AsyncMock()):
        # 不应抛任何异常
        result = await submit_clarify_response(
            trace_id=trace_id,
            body=ClarifyResponseRequest(pending_id=999, answer="no"),
            user=fake_user,
            db=fake_db,
        )

    assert result == {"ok": True}
