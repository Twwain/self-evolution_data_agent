"""Stage 5 Task 3 — LLM 收到 execute_query truncated error 后切换走 plan 的集成验证.

防死循环: 验证 agent loop 在收到 forced-plan error 后下一轮可调 generate_query_plan
(而非无限重试 execute_query single).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from app.engine.agent_loop import run_agent_loop
from app.engine.llm import ToolCall, ToolUseResponse


@dataclass
class _ScriptLLM:
    responses: list[ToolUseResponse]
    calls: list[list[dict]] = field(default_factory=list)

    async def __call__(self, messages, tools, stream_callback=None, **_kw):
        self.calls.append([dict(m) for m in messages])
        await asyncio.sleep(0)
        if not self.responses:
            return ToolUseResponse(text="done", tool_calls=[],
                                   stop_reason="end_turn", usage={})
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_truncated_single_forces_plan_path():
    """execute_query(single) 返回 truncated error → 下一轮调 generate_query_plan."""
    # execute_query stub: 模拟 _maybe_force_plan 已把截断结果转成引导 error
    async def fake_execute_query(**kwargs):
        return {"error": "result_truncated_use_plan",
                "message": "结果已被截断",
                "suggestion": "改用 generate_query_plan 生成查询计划, 再用 execute_plan 执行."}

    async def fake_generate_plan(**kwargs):
        return {"plan": {"strategy": "single_aggregate", "steps": []}}

    async def fake_execute_plan(**kwargs):
        return {"rows": [{"a": 1}], "columns": ["a"], "truncated": False}

    async def fake_present(**kwargs):
        return {"status": "ok", "ref": "c3", "chart_spec": {"chart_type": "table"}}

    tools_registry = {
        "execute_query": fake_execute_query,
        "generate_query_plan": fake_generate_plan,
        "execute_plan": fake_execute_plan,
        "present_result": fake_present,
    }

    llm = _ScriptLLM(responses=[
        ToolUseResponse(text="", stop_reason="tool_use", usage={}, tool_calls=[
            ToolCall(id="c1", name="execute_query",
                     input={"mode": "single", "query": {"sql": "SELECT 1"}})]),
        ToolUseResponse(text="", stop_reason="tool_use", usage={}, tool_calls=[
            ToolCall(id="c2", name="generate_query_plan", input={"question": "q"})]),
        ToolUseResponse(text="完成", stop_reason="end_turn", usage={}, tool_calls=[]),
    ])

    events: list[dict] = []
    async def emit(evt):
        events.append(evt)

    result = await run_agent_loop(
        trace_id="t-forced-plan",
        question="查询所有数据",
        tools_registry=tools_registry,
        tool_specs=[],
        sse_emit=emit,
        user_correction_queue=asyncio.Queue(),
        llm=llm,
        system_prompt="test",
    )

    # 第二轮调了 generate_query_plan (非重试 execute_query single)
    plan_names = [t["name"] for t in result.tool_trace]
    assert "execute_query" in plan_names
    assert "generate_query_plan" in plan_names  # 证明 LLM 遵守引导走 plan
