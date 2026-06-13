"""Stage 2 — tool_trace 项含 id (tc.id), 使 present_result.ref 可反查."""
from __future__ import annotations

import asyncio

import pytest

from app.engine.agent_loop import _exec_tool
from app.engine.llm import ToolCall


@pytest.mark.asyncio
async def test_exec_tool_output_shape_unchanged():
    # _exec_tool 不负责 id; 验证它仍返回 {status, output}
    async def fake(**kw):
        return {"ok": 1}
    tc = ToolCall(id="call_abc", name="fake", input={})
    out = await _exec_tool(tc, {"fake": fake}, asyncio.Semaphore(1))
    assert out["status"] == "ok"
    assert out["output"] == {"ok": 1}
