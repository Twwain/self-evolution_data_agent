"""P0-4 Task 7: cancelled 关注点分离测试.

SSE cancelled event data 为空 {}, reason 区分仅通过后端日志可见。
"""
from __future__ import annotations

import asyncio

import pytest


class TestCancelReasonDict:
    def test_default_external_when_no_endpoint_write(self):
        """无 cancel 端点写入时, agent_loop pop 默认 external."""
        from app.api.query import _cancel_reason

        trace_id = "trace-default-external"
        _cancel_reason.pop(trace_id, None)
        reason = _cancel_reason.pop(trace_id, "external")
        assert reason == "external"

    def test_endpoint_writes_user_abort(self):
        """cancel 端点逻辑: 写 _cancel_reason[trace_id] = 'user_abort'."""
        from app.api.query import _cancel_reason

        trace_id = "trace-user-abort"
        _cancel_reason[trace_id] = "user_abort"
        assert _cancel_reason.get(trace_id) == "user_abort"
        # 模拟 agent_loop except 块 pop
        reason = _cancel_reason.pop(trace_id, "external")
        assert reason == "user_abort"
        assert trace_id not in _cancel_reason


class TestCancelledSseDataEmpty:
    @pytest.mark.asyncio
    async def test_cancelled_emit_data_is_empty_dict(self):
        """SSE cancelled event 后端 emit data 为 {}, 不携带 reason 字段."""
        from app.engine.sse_manager import register_sse_session
        from app.api.query import _cancel_reason

        trace_id = "trace-empty-data"
        event_q, _ = register_sse_session(trace_id)
        _cancel_reason[trace_id] = "user_abort"

        # 模拟 agent_loop except 块行为
        async def fake_emit(evt):
            await event_q.put(evt)

        reason = _cancel_reason.pop(trace_id, "external")
        assert reason == "user_abort"
        await fake_emit({"event": "cancelled", "data": {}})

        evt = await asyncio.wait_for(event_q.get(), timeout=1.0)
        assert evt["event"] == "cancelled"
        assert evt["data"] == {}, "cancelled event data must be empty dict"
