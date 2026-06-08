"""Stage extractor-protocol Task 1 — _exec_tool 错误路径 output 必须是 dict.

不变量: tool_trace[].output 始终是 dict.
- success: tool 自然输出 (dict 由 tool 自己保证)
- error:   {"error_type": str, "error_message": str}
"""
import asyncio

from app.engine.agent_loop import _exec_tool
from app.engine.llm import ToolCall


def _make_tc(name: str = "test_tool", inp: dict | None = None) -> ToolCall:
    return ToolCall(id="tc1", name=name, input=inp or {})


def test_unknown_tool_returns_dict_output():
    sem = asyncio.Semaphore(1)
    res = asyncio.run(_exec_tool(_make_tc(name="nonexistent"), {}, sem))
    assert res["status"] == "error"
    assert isinstance(res["output"], dict)
    assert res["output"]["error_type"] == "UnknownToolError"
    assert "nonexistent" in res["output"]["error_message"]


def test_tool_raises_returns_dict_output():
    async def boom():
        raise ValueError("simulated $lookup failure")

    sem = asyncio.Semaphore(1)
    res = asyncio.run(_exec_tool(_make_tc(name="boom"), {"boom": boom}, sem))
    assert res["status"] == "error"
    assert isinstance(res["output"], dict)
    assert res["output"]["error_type"] == "ValueError"
    assert "simulated" in res["output"]["error_message"]


def test_tool_success_passes_through_dict():
    async def ok():
        return {"rows": [{"a": 1}], "count": 1}

    sem = asyncio.Semaphore(1)
    res = asyncio.run(_exec_tool(_make_tc(name="ok"), {"ok": ok}, sem))
    assert res["status"] == "ok"
    assert res["output"] == {"rows": [{"a": 1}], "count": 1}


def test_cancelled_error_propagates():
    async def cancel():
        raise asyncio.CancelledError()

    sem = asyncio.Semaphore(1)
    try:
        asyncio.run(_exec_tool(_make_tc(name="cancel"), {"cancel": cancel}, sem))
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("expected CancelledError to propagate")
